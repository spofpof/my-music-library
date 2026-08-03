import os
import re
import requests
import mutagen

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

def get_real_album_art(artist, title):
    """Searches iTunes API using primary artist + title, with a title-only fallback."""
    try:
        # Extract only the primary artist (e.g., drop 'feat. SmallX' or '& Other' for the search query)
        primary_artist = re.split(r'\b(feat\.?|ft\.?|featuring|&|,)\b', artist, flags=re.IGNORECASE)[0].strip()
        
        # Attempt 1: Search by Primary Artist + Title
        query = f"{primary_artist} {title}"
        api_url = f"https://itunes.apple.com/search?term={requests.utils.quote(query)}&entity=song&limit=1"
        response = requests.get(api_url)
        
        if response.status_code == 200:
            data = response.json()
            results = data.get("results", [])
            if results:
                artwork_url = results[0].get("artworkUrl100", "")
                if artwork_url:
                    return artwork_url.replace("100x100bb.jpg", "600x600bb.jpg")

        # Attempt 2 (Fallback): If artist+title failed, try searching by just the song Title
        if title:
            title_url = f"https://itunes.apple.com/search?term={requests.utils.quote(title)}&entity=song&limit=1"
            res_title = requests.get(title_url)
            if res_title.status_code == 200:
                data_t = res_title.json()
                results_t = data_t.get("results", [])
                if results_t:
                    artwork_url = results_t[0].get("artworkUrl100", "")
                    if artwork_url:
                        return artwork_url.replace("100x100bb.jpg", "600x600bb.jpg")
                        
    except Exception as e:
        print(f"Could not fetch real artwork: {e}")
        
    print(f"⚠️ iTunes found no match for '{artist} - {title}', using placeholder.")
    return "https://picsum.photos/400/400"

def add_song_to_firebase(title, artist, raw_url, artwork_url):
    """Pushes new song metadata and real artwork to Firebase Firestore."""
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

                # 2. Extract true embedded ID3 tags using Mutagen (with fallback to filename)
                try:
                    audio = mutagen.File(file_path, easy=True)
                    if audio is not None:
                        if 'title' in audio and audio['title']:
                            title = audio['title'][0].strip()
                        if 'artist' in audio and audio['artist']:
                            artist = audio['artist'][0].strip()
                except Exception as e:
                    print(f"⚠️ Could not read ID3 tags for {file}, using filename fallback: {e}")
                    
                print(f"🎵 Found track: {title} by {artist}")
                
                # 3. Fetch artwork using the cleaned primary artist name and exact tags/filename data
                print("🖼️ Searching for real album artwork...")
                artwork_url = get_real_album_art(artist, title)
                
                success = add_song_to_firebase(title, artist, raw_url, artwork_url)
                if success:
                    print(f"✅ Successfully registered with cover art: {rel_path_url}")
                    synced_count += 1
                else:
                    print(f"❌ Failed to register in Firebase: {rel_path_url}")
                    
    print(f"✨ Sync complete! {synced_count} new track(s) added.")

if __name__ == "__main__":
    sync_music()
