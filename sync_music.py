import os
import re
import json
import base64
import requests
import mutagen
import mutagen.id3

# --- CONFIGURATION (Loaded securely from Environment Variables / GitHub Secrets) ---
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GDRIVE_FOLDER_ID = os.getenv("GDRIVE_FOLDER_ID")

def load_existing_songs():
    """Loads existing songs from local songs.json file to avoid duplicates."""
    if os.path.exists("songs.json"):
        try:
            with open("songs.json", "r", encoding="utf-8") as f:
                songs = json.load(f)
                urls = {song.get("url") for song in songs if "url" in song}
                return songs, urls
        except Exception as e:
            print(f"⚠️ Error reading existing songs.json: {e}")
    return [], set()

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
    except Exception:
        pass
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

def get_fallback_album(artist, title):
    """Fallback to Deezer or iTunes to find the album name if missing from ID3 tags."""
    primary_artist = re.split(r'\b(feat\.?|ft\.?|featuring|&|,)\b', artist, flags=re.IGNORECASE)[0].strip()
    clean_title = re.sub(r'\s*[\(\[].*?(feat|ft|live|mix).*?[\)\]]', '', title, flags=re.IGNORECASE).strip()
    if not clean_title:
        clean_title = title

    query = f"{primary_artist} {clean_title}"

    try:
        deezer_url = f"https://api.deezer.com/search?q={requests.utils.quote(query)}"
        response = requests.get(deezer_url)
        if response.status_code == 200:
            data = response.json().get("data", [])
            if data:
                album_obj = data[0].get("album", {})
                if album_obj.get("title"):
                    return album_obj.get("title")
    except Exception:
        pass

    try:
        itunes_url = f"https://itunes.apple.com/search?term={requests.utils.quote(query)}&entity=song&limit=1"
        response = requests.get(itunes_url)
        if response.status_code == 200:
            results = response.json().get("results", [])
            if results and results[0].get("collectionName"):
                return results[0].get("collectionName")
    except Exception:
        pass

    return "Unknown Album"

def sync_music():
    if not GOOGLE_API_KEY or not GDRIVE_FOLDER_ID:
        print("❌ Error: Missing configuration environment variables (GOOGLE_API_KEY or GDRIVE_FOLDER_ID).")
        return

    print("🔍 Fetching song list directly from Google Drive folder...")
    
    songs_list, existing_urls = load_existing_songs()
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
                print(f"⏩ Already in songs.json: {file_name}")
                continue
                
            # Temporarily download file to read deep ID3 metadata & properties
            temp_filename = "temp_song.mp3"
            try:
                file_resp = requests.get(audio_url, stream=True)
                if file_resp.status_code == 200:
                    with open(temp_filename, "wb") as f:
                        for chunk in file_resp.iter_content(chunk_size=8192):
                            f.write(chunk)
                else:
                    print(f"❌ Failed to download {file_name} from Drive")
                    continue
            except Exception as e:
                print(f"❌ Error downloading {file_name}: {e}")
                continue

            # 1. Parse filename defaults
            clean_name = file_name.replace(".mp3", "")
            if " - " in clean_name:
                fallback_artist, fallback_title = clean_name.split(" - ", 1)
            else:
                fallback_artist = "Unknown Artist"
                fallback_title = clean_name
                
            title = fallback_title.strip()
            artist = fallback_artist.strip()
            album = "Unknown Album"
            genre = "Unknown Genre"
            year = "Unknown Year"
            track_number = "0"
            duration = 0
            bitrate = 0
            artwork_url = None

            # 2. Extract deep ID3 tags and technical properties using Mutagen
            try:
                audio = mutagen.File(temp_filename, easy=True)
                if audio is not None:
                    if 'title' in audio and audio['title']:
                        title = audio['title'][0].strip()
                    if 'artist' in audio and audio['artist']:
                        artist = audio['artist'][0].strip()
                    if 'album' in audio and audio['album']:
                        album = audio['album'][0].strip()
                    if 'genre' in audio and audio['genre']:
                        genre = audio['genre'][0].strip()
                    if 'date' in audio and audio['date']:
                        year = str(audio['date'][0]).strip()
                    elif 'year' in audio and audio['year']:
                        year = str(audio['year'][0]).strip()
                    if 'tracknumber' in audio and audio['tracknumber']:
                        track_number = str(audio['tracknumber'][0]).strip()
            
                # Extract technical audio properties (duration, bitrate)
                audio_full = mutagen.File(temp_filename)
                if audio_full is not None and hasattr(audio_full, 'info'):
                    if hasattr(audio_full.info, 'length'):
                        duration = round(audio_full.info.length, 2)
                    if hasattr(audio_full.info, 'bitrate'):
                        bitrate = audio_full.info.bitrate
            except Exception as e:
                print(f"⚠️ Could not read full ID3 tags for {file_name}: {e}")

            # 3. Extract embedded artwork from MP3
            artwork_url = extract_embedded_artwork(temp_filename)

            # Clean up temp file immediately
            if os.path.exists(temp_filename):
                os.remove(temp_filename)

            # 4. Fallback to APIs if metadata is missing
            if album == "Unknown Album" or not album:
                album = get_fallback_album(artist, title)

            if not artwork_url:
                artwork_url = get_fallback_artwork(artist, title)

            print(f"🎵 Track: {title} | Artist: {artist} | Album: {album} | Genre: {genre} | Year: {year}")
            
            metadata = {
                "title": title,
                "artist": artist,
                "album": album,
                "genre": genre,
                "year": year,
                "trackNumber": track_number,
                "duration": duration,
                "bitrate": bitrate,
                "artwork": artwork_url,
                "url": audio_url
            }

            songs_list.append(metadata)
            existing_urls.add(audio_url)
            synced_count += 1
            print(f"✅ Added track to local queue: {file_name}")
            
        page_token = data.get("nextPageToken")
        if not page_token:
            break
            
    # Save the complete metadata list to songs.json
    try:
        with open("songs.json", "w", encoding="utf-8") as f:
            json.dump(songs_list, f, indent=4, ensure_ascii=False)
        print(f"💾 Successfully updated songs.json! Total tracks in library: {len(songs_list)}")
    except Exception as e:
        print(f"❌ Error writing to songs.json: {e}")

    print(f"✨ Sync complete! {synced_count} new track(s) added.")

if __name__ == "__main__":
    sync_music()
