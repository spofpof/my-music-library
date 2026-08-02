import os
import requests

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
    """Searches the iTunes API for the real official album cover art."""
    try:
        query = f"{artist} {title}"
        api_url = f"https://itunes.apple.com/search?term={requests.utils.quote(query)}&entity=song&limit=1"
        response = requests.get(api_url)
        
        if response.status_code == 200:
            data = response.json()
            results = data.get("results", [])
            if results:
                # Get the 100x100 image and scale it up to high resolution (600x600)
                artwork_url = results[0].get("artworkUrl100", "")
                if artwork_url:
                    return artwork_url.replace("100x100bb.jpg", "600x600bb.jpg")
    except Exception as e:
        print(f"Could not fetch real artwork: {e}")
        
    # Fallback placeholder if song isn't found on iTunes
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
                rel_path = os.path.relpath(os.path.join(root, file), ".")
                rel_path_url = rel_path.replace("\\", "/")
                
                raw_url = f"https://raw.githubusercontent.com/{GITHUB_USERNAME}/{GITHUB_REPO}/{GITHUB_BRANCH}/{rel_path_url}"
                
                if raw_url in existing_urls:
                    print(f"⏩ Already synced: {rel_path_url}")
                    continue
                
                # Parse Artist and Title from filename (Format: Artist - Title.mp3)
                clean_name = file.replace(".mp3", "")
                if " - " in clean_name:
                    artist, title = clean_name.split(" - ", 1)
                else:
                    artist = "Unknown Artist"
                    title = clean_name
                    
                print(f"🎵 Found new track: {title} by {artist}")
                
                # Automatically fetch the real album art online
                print("🖼️ Searching for real album artwork...")
                artwork_url = get_real_album_art(artist.strip(), title.strip())
                
                success = add_song_to_firebase(title.strip(), artist.strip(), raw_url, artwork_url)
                if success:
                    print(f"✅ Successfully registered with real cover art: {rel_path_url}")
                    synced_count += 1
                else:
                    print(f"❌ Failed to register in Firebase: {rel_path_url}")
                    
    print(f"✨ Sync complete! {synced_count} new track(s) added.")

if __name__ == "__main__":
    sync_music()
