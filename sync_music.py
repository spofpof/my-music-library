import os
import re
import base64
import requests
import mutagen

# --- CONFIGURATION ---
GITHUB_USERNAME = "spofpof"
GITHUB_REPO = "my-music-library"
GITHUB_BRANCH = "main"

GH_PAT = os.getenv("GH_PAT")
FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID")
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")

def get_spotify_token():
    """Authenticates with Spotify API using Client Credentials flow to get an access token."""
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        return None
    try:
        auth_str = f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}"
        b64_auth = base64.b64encode(auth_str.encode()).decode()
        headers = {"Authorization": f"Basic {b64_auth}"}
        data = {"grant_type": "client_credentials"}
        response = requests.post("https://accounts.spotify.com/api/token", headers=headers, data=data)
        if response.status_code == 200:
            return response.json().get("access_token")
    except Exception as e:
        print(f"Error getting Spotify token: {e}")
    return None

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
    """Searches Spotify API for track cover art using flexible free-text queries and cleaning."""
    token = get_spotify_token()
    if not token:
        print("⚠️ Missing Spotify API credentials, using placeholder artwork.")
        return "https://picsum.photos/400/400"

    headers = {"Authorization": f"Bearer {token}"}
    
    try:
        # 1. Extract primary artist (dropping features, commas, etc.)
        primary_artist = re.split(r'\b(feat\.?|ft\.?|featuring|&|,)\b', artist, flags=re.IGNORECASE)[0].strip()
        
        # 2. Clean track title by removing parenthetical features (e.g., "(feat. ...)")
        clean_title = re.sub(r'\s*[\(\[].*?(feat|ft|live|mix).*?[\)\]]', '', title, flags=re.IGNORECASE).strip()
        if not clean_title:
            clean_title = title

        # Attempt 1: Flexible free-text search (Primary Artist + Clean Title)
        query = f"{primary_artist} {clean_title}"
        search_url = f"https://api.spotify.com/v1/search?q={requests.utils.quote(query)}&type=track&limit=1"
        response = requests.get(search_url, headers=headers)
        
        if response.status_code == 200:
            items = response.json().get("tracks", {}).get("items", [])
            if items:
                images = items[0].get("album", {}).get("images", [])
                if images:
                    return images[0].get("url")

        # Attempt 2 (Fallback): Search by clean title only
        query_title = f"{clean_title}"
        search_url_t = f"https://api.spotify.com/v1/search?q={requests.utils.quote(query_title)}&type=track&limit=1"
        response_t = requests.get(search_url_t, headers=headers)
        if response_t.status_code == 200:
            items_t = response_t.json().get("tracks", {}).get("items", [])
            if items_t:
                images_t = items_t[0].get("album", {}).get("images", [])
                if images_t:
                    return images_t[0].get("url")

    except Exception as e:
        print(f"Could not fetch Spotify artwork: {e}")
        
    print(f"⚠️ Spotify found no match for '{artist} - {title}', using placeholder.")
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
                
                # 3. Fetch cover art from Spotify
                print("🖼️ Fetching cover art from Spotify...")
                artwork_url = get_real_album_art(artist, title)
                
                success = add_song_to_firebase(title, artist, raw_url, artwork_url)
                if success:
                    print(f"✅ Successfully registered with Spotify cover: {rel_path_url}")
                    synced_count += 1
                else:
                    print(f"❌ Failed to register in Firebase: {rel_path_url}")
                    
    print(f"✨ Sync complete! {synced_count} new track(s) added.")

if __name__ == "__main__":
    sync_music()
