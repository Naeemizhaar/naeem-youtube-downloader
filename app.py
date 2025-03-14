from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit
import yt_dlp
import os
import threading
from datetime import datetime, timedelta
import logging

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app, async_mode='threading')

# Setup logging to CMD
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

# Default download folder
DOWNLOAD_PATH = "downloads"
if not os.path.exists(DOWNLOAD_PATH):
    os.makedirs(DOWNLOAD_PATH)

links = []
mode = "Unlimited Downloader"
download_active = False

# Custom logger for yt_dlp output
class YTDLPLogger:
    def debug(self, msg):
        logging.debug(f"YTDLP Debug: {msg}")
        socketio.emit('progress_update', {'message': msg.strip()})
        socketio.sleep(0)

    def info(self, msg):
        logging.info(f"YTDLP Info: {msg}")
        socketio.emit('progress_update', {'message': msg.strip()})
        socketio.sleep(0)

    def warning(self, msg):
        logging.warning(f"YTDLP Warning: {msg}")
        socketio.emit('progress_update', {'message': f"WARNING: {msg.strip()}"})
        socketio.sleep(0)

    def error(self, msg):
        logging.error(f"YTDLP Error: {msg}")
        socketio.emit('progress_update', {'message': f"ERROR: {msg.strip()}"})
        socketio.sleep(0)

def download_hook(d):
    if d['status'] == 'downloading':
        percent = d.get('_percent_str', '0%').replace('%', '')
        speed = d.get('_speed_str', '0 KB/s')
        eta = d.get('eta', 'N/A')
        total_bytes = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
        size_mb = total_bytes / (1024 * 1024)
        message = f"Downloading: {percent}% at {speed}, ETA: {eta}s"
        logging.info(message)
        socketio.emit('progress_update', {
            'percent': percent,
            'speed': speed,
            'eta': eta,
            'size': f"{size_mb:.2f} MB",
            'message': message
        })
        socketio.sleep(0)
    elif d['status'] == 'finished':
        message = 'Download complete!'
        logging.info(message)
        socketio.emit('progress_update', {
            'message': message,
            'percent': '100',
            'speed': '0 KB/s',
            'eta': '0',
            'size': f"{d.get('total_bytes', 0) / (1024 * 1024):.2f} MB"
        })
        socketio.sleep(0)

def download_video(link):
    global download_active
    try:
        logging.info(f"Starting download process for link: {link}")
        current_time = datetime.now()
        time_24_hours_ago = current_time - timedelta(hours=24)

        playlist_limit = 100 if mode == "Unlimited Downloader" else 10

        # Step 1: Check if the link is a playlist or single video
        ydl_opts_check = {
            'extract_flat': True,  # Minimal metadata for checking
            'logger': YTDLPLogger(),
        }

        logging.info("Checking if link is a playlist or single video...")
        with yt_dlp.YoutubeDL(ydl_opts_check) as ydl:
            info = ydl.extract_info(link, download=False)

        videos_downloaded = 0
        entries = []

        if 'entries' in info and len(info['entries']) > 0:  # Playlist
            logging.info(f"Playlist detected with {len(info['entries'])} videos")
            entries = info['entries']
        else:  # Single video
            logging.info("Single video detected")
            entries = [info]  # Treat single video as a single-entry list

        # Step 2: Process the entries (playlist or single video)
        for entry in entries:
            if videos_downloaded >= playlist_limit:
                break

            video_url = entry.get('url', entry.get('webpage_url') or entry.get('id', ''))
            if not video_url:
                continue

            if not video_url.startswith('http'):
                video_url = f"https://www.youtube.com/watch?v={video_url}"

            # Step 3: Download video with minimal metadata fetching
            ydl_opts_video = {
                'outtmpl': os.path.join(DOWNLOAD_PATH, '%(title)s.%(ext)s'),
                'format': 'best[ext=mp4][height<=360]/best[ext=mp4]',  # MP4 format
                'logger': YTDLPLogger(),
                'progress_hooks': [download_hook],
                'no_cache_dir': True,  # Avoid unnecessary caching
                'noplaylist': True,    # Treat as single video
            }

            with yt_dlp.YoutubeDL(ydl_opts_video) as ydl:
                video_info = ydl.extract_info(video_url, download=False)

                upload_date = video_info.get('upload_date', '19700101')
                upload_datetime = datetime.strptime(upload_date, "%Y%m%d")

                if mode == "24 Hours Downloader" and (upload_datetime < time_24_hours_ago or upload_datetime > current_time):
                    message = f"Video uploaded on {upload_date} is outside 24-hour range. Skipping."
                    logging.info(message)
                    socketio.emit('progress_update', {'message': message})
                    socketio.sleep(0)
                    continue

                message = f"Starting download for {video_url}"
                logging.info(message)
                socketio.emit('progress_update', {'message': message})
                socketio.sleep(0)

                ydl.download([video_url])
                videos_downloaded += 1

        message = f"Processed {videos_downloaded} videos. Downloaded: {videos_downloaded}"
        logging.info(message)
        return {'status': 'success', 'message': message}
    except Exception as e:
        error_message = f"Error during download: {str(e)}"
        logging.error(error_message)
        socketio.emit('progress_update', {'message': error_message})
        socketio.sleep(0)
        return {'status': 'error', 'message': error_message}
    finally:
        download_active = False
        logging.info("Download process finished")

@app.route('/')
def index():
    return render_template('index.html', links=links, mode=mode)

@app.route('/add', methods=['POST'])
def add():
    link = request.form['link']
    if "youtube.com" in link or "youtu.be" in link:
        links.append(link)
        message = f"Link added: {link}"
        logging.info(message)
        return jsonify({"message": message, "links": links})
    message = "Invalid YouTube link"
    logging.warning(message)
    return jsonify({"message": message}), 400

@app.route('/delete', methods=['POST'])
def delete():
    index = int(request.form['index'])
    if 0 <= index < len(links):
        deleted_link = links.pop(index)
        message = f"Deleted: {deleted_link}"
        logging.info(message)
        return jsonify({"message": message, "links": links})
    message = "Select a link first"
    logging.warning(message)
    return jsonify({"message": message}), 400

@app.route('/set_mode', methods=['POST'])
def set_mode():
    global mode
    mode = request.form['mode']
    message = f"Mode set to: {mode}"
    logging.info(message)
    return jsonify({"message": message, "mode": mode})

@app.route('/start_download', methods=['POST'])
def start_download():
    global download_active
    if download_active or not links:
        message = "Download running or no links!"
        logging.warning(message)
        return jsonify({"message": message}), 400
    download_active = True
    logging.info("Starting download thread...")
    threading.Thread(target=process_download).start()
    return jsonify({"message": "Download started..."})

def process_download():
    global download_active
    try:
        for link in links[:]:
            logging.info(f"Processing link: {link}")
            result = download_video(link)
            if result['status'] == 'success':
                links.remove(link)
            socketio.emit('progress_update', {'message': result['message']})
            socketio.sleep(0)
    except Exception as e:
        error_message = f"Error in process_download: {str(e)}"
        logging.error(error_message)
        socketio.emit('progress_update', {'message': error_message})
        socketio.sleep(0)
    finally:
        download_active = False
        logging.info("Download thread finished")

if __name__ == '__main__':
    socketio.run(app, debug=True)