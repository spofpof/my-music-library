import os
import re
import base64
import requests
import mutagen
import mutagen.id3

# --- CONFIGURATION ---
GITHUB_USERNAME = "spofpof"
GITHUB_REPO = "my-music-library"
GITHUB_BRANCH = "main"

GH_PAT = os.getenv("GH_PAT")
FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID")

def get_existing_firebase_urls():
    """Fetches all existing song URLs from Firebase Firestore to avoid duplicates."""
    firebase_url = f"https://firestore.googleapis.com/v1/projects/{FIREBASE_PROJECT_ID}/databases/(default)/documents/songs"
    try:
        response = requests.get(firebase_url)
        if response.status_code == 200:
            data = response.json()
            existing_urls = set()
            for doc in data.get("documents", []):
                fields = doc.get("fields", {})
                url_field = fields.get("url", {}).get("stringValue")
                if url_field:
                    existing_urls.add(url_field)
            return existing_urls
    except Exception as e:
        print(f"Error fetching from Firebase: {e}")
    return set()

def extract_embedded_artwork(file_path):
    """Extracts embedded cover art from the MP3 file if it exists."""
    try:
        audio = mutagen.File(file_path)
        if audio is not None and audio.tags is not None:
            for tag in audio.tags.values():
                if isinstance(tag, mutagen.id3.APIC):
                    image_data = tag.data
                    mime_type = tag.mime or "image/jpeg"
                    b64_encoded = base64.b64encode(image_data).decode('utf-8')
                    return f"data:{mime_type};base64,{b64_encoded}"
    except Exception as e:
        print(f"Error extracting embedded artwork: {e}")
    return None

def get_fallback_artwork(artist, title):
    """Fallback to Deezer or iTunes if no embedded artwork is found."""
    primary_artist = re.split(r'\b(feat\.?|ft\.?|featuring|&|,)\b', artist, flags=re.IGNORECASE)[0].strip()
    clean_title = re.sub(r'\s*[\(\[].*?(feat|ft|live|mix).*?[\)\]]', '', title, flags=re.IGNORECASE).strip()
    if not clean_title:
        clean_title = title

    query = f"{primary_artist} {clean_title}"

    # Try Deezer API
    try:
        deezer_url = f"https://api.deezer.com/search?q={requests.utils.quote(query)}"
        response = requests.get(deezer_url)
        if response.status_code == 200:
            data = response.json().get("data", [])
            if data:
                album = data[0].get("album", {})
                artwork = album.get("cover_xl") or album.get("cover_big") or album.get("cover_medium")
                if artwork:
                    return artwork
    except Exception:
        pass

    # Try iTunes API Fallback
    try:
        itunes_url = f"https://itunes.apple.com/search?term={requests.utils.quote(query)}&entity=song&limit=1"
        response = requests.get(itunes_url)
        if response.status_code == 200:
            results = response.json().get("results", [])
            if results:
                artwork_url = results[0].get("artworkUrl100", "")
                if artwork_url:
                    return artwork_url.replace("100x100bb.jpg", "600x600bb.jpg")
    except Exception:
        pass

    return "https://picsum.photos/400/400"

def add_song_to_firebase(title, artist, raw_url, artwork_url):
    """Pushes new song metadata and cover art to Firebase Firestore."""
    firebase_url = f"https://firestore.googleapis.com/v1/projects/{FIREBASE_PROJECT_ID}/databases/(default)/documents/songs"
    payload = {
        "fields": {
            "title": {"stringValue": title},
            "artist": {"stringValue": artist},
            "artwork": {"stringValue": artwork_url},
            "url": {"stringValue": raw_url}
        }
    }
    response = requests.post(firebase_url, json=payload)
    return response.status_code == 200

def sync_music():
    print("🔍 Scanning repository for MP3 files...")
    
    existing_urls = get_existing_firebase_urls()
    synced_count = 0
    
    for root, dirs, files in os.walk("."):
        if ".github" in root or ".git" in root:
            continue
            
        for file in files:
            if file.lower().endswith(".mp3"):
                file_path = os.path.join(root, file)
                rel_path = os.path.relpath(file_path, ".")
                rel_path_url = rel_path.replace("\\", "/")
                
                raw_url = f"https://raw.githubusercontent.com/{GITHUB_USERNAME}/{GITHUB_REPO}/{GITHUB_BRANCH}/{rel_path_url}"
                
                if raw_url in existing_urls:
                    print(f"⏩ Already synced: {rel_path_url}")
                    continue
                
                # 1. Parse fallback artist and title from filename
                clean_name = file.replace(".mp3", "")
                if " - " in clean_name:
                    fallback_artist, fallback_title = clean_name.split(" - ", 1)
                else:
                    fallback_artist = "Unknown Artist"
                    fallback_title = clean_name
                    
                title = fallback_title.strip()
                artist = fallback_artist.strip()

                # 2. Extract true embedded ID3 tags using Mutagen
                try:
                    audio = mutagen.File(file_path, easy=True)
                    if audio is not None:
                        if 'title' in audio and audio['title']:
                            title = audio['title'][0].strip()
                        if 'artist' in audio and audio['artist']:
                            artist = audio['artist'][0].strip()
                except Exception as e:
                    print(f"⚠️ Could not read ID3 tags for {file}: {e}")
                    
                print(f"🎵 Found track: {title} by {artist}")
                
                # 3. Get artwork (Embedded first, then API fallback)
                print("🖼️ Checking for embedded cover art...")
                artwork_url = extract_embedded_artwork(file_path)
                
                if artwork_url:
                    print("✅ Found embedded cover art in MP3 file!")
                else:
                    print("🌐 No embedded cover found, searching online APIs...")
                    artwork_url = get_fallback_artwork(artist, title)
                
                success = add_song_to_firebase(title, artist, raw_url, artwork_url)
                if success:
                    print(f"✅ Successfully registered track: {rel_path_url}")
                    synced_count += 1
                else:
                    print(f"❌ Failed to register in Firebase: {rel_path_url}")
                    
    print(f"✨ Sync complete! {synced_count} new track(s) added.")

if __name__ == "__main__":
    sync_music()
