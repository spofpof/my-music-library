import os
import re
import requests

# --- CONFIGURATION (Loaded securely from Environment Variables / GitHub Secrets) ---
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GDRIVE_FOLDER_ID = os.getenv("GDRIVE_FOLDER_ID")
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

def add_song_to_firebase(title, artist, audio_url, artwork_url):
    """Pushes new song metadata and cover art to Firebase Firestore."""
    firebase_url = f"https://firestore.googleapis.com/v1/projects/{FIREBASE_PROJECT_ID}/databases/(default)/documents/songs"
    payload = {
        "fields": {
            "title": {"stringValue": title},
            "artist": {"stringValue": artist},
            "artwork": {"stringValue": artwork_url},
            "url": {"stringValue": audio_url}
        }
    }
    response = requests.post(firebase_url, json=payload)
    return response.status_code == 200

def sync_music():
    if not GOOGLE_API_KEY or not GDRIVE_FOLDER_ID or not FIREBASE_PROJECT_ID:
        print("❌ Error: Missing configuration environment variables (GOOGLE_API_KEY, GDRIVE_FOLDER_ID, or FIREBASE_PROJECT_ID).")
        return

    print("🔍 Fetching song list directly from Google Drive folder...")
    
    existing_urls = get_existing_firebase_urls()
    synced_count = 0
    page_token = None
    
    while True:
        url = f"https://www.googleapis.com/drive/v3/files?q='{GDRIVE_FOLDER_ID}'%20in%20parents%20and%20trashed=false&key={GOOGLE_API_KEY}&pageSize=1000"
        if page_token:
            url += f"&pageToken={page_token}"
            
        response = requests.get(url)
        if response.status_code != 200:
            print(f"❌ Error communicating with Google Drive API: {response.text}")
            break
            
        data = response.json()
        files = data.get("files", [])
        
        for item in files:
            file_name = item.get("name")
            file_id = item.get("id")
            
            if not file_name or not file_name.lower().endswith(".mp3"):
                continue
                
            audio_url = f"https://drive.google.com/uc?export=download&id={file_id}"
            
            if audio_url in existing_urls:
                print(f"⏩ Already synced: {file_name}")
                continue
                
            # Parse artist and title from filename (Format: "Artist - Title.mp3")
            clean_name = file_name.replace(".mp3", "")
            if " - " in clean_name:
                fallback_artist, fallback_title = clean_name.split(" - ", 1)
            else:
                fallback_artist = "Unknown Artist"
                fallback_title = clean_name
                
            title = fallback_title.strip()
            artist = fallback_artist.strip()
            
            print(f"🎵 Found track: {title} by {artist}")
            
            # Get artwork via online APIs
            print("🖼️ Searching online for cover art...")
            artwork_url = get_fallback_artwork(artist, title)
            
            success = add_song_to_firebase(title, artist, audio_url, artwork_url)
            if success:
                print(f"✅ Successfully registered track: {file_name}")
                synced_count += 1
            else:
                print(f"❌ Failed to register in Firebase: {file_name}")
                
        page_token = data.get("nextPageToken")
        if not page_token:
            break
            
    print(f"✨ Sync complete! {synced_count} new track(s) added.")

if __name__ == "__main__":
    sync_music()
