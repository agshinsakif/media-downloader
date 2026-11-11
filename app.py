import os
import re
import tempfile
from pathlib import Path
from flask import Flask, request, jsonify, send_file, render_template, after_this_request
import yt_dlp

app = Flask(__name__)

PRIORITY_LANG_CODES = ["en", "hu", "fr", "de", "az", "tr"]

def truncate_filename(title: str, max_words: int = 3) -> str:
    if " | " in title:
        title = title.split(" | ", 1)[-1]
    clean_title = re.sub(r'[^\w\s-]', '', title).strip()
    words = clean_title.split()
    if len(words) > max_words:
        return " ".join(words[:max_words])
    return clean_title or "media_file"

def convert_vtt_to_txt(vtt_filepath: str, txt_filepath: str):
    if not os.path.exists(vtt_filepath): return
    with open(vtt_filepath, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    clean_lines, seen_lines = [], set()
    for line in lines:
        line_str = line.strip()
        if not line_str or line_str.startswith("WEBVTT") or line_str.startswith("Kind:") or line_str.startswith("Language:"): continue
        if "-->" in line_str or re.match(r'^\d+$', line_str): continue
        clean_text = re.sub(r'<[^>]+>', '', line_str).strip()
        if clean_text and clean_text not in seen_lines:
            seen_lines.add(clean_text)
            clean_lines.append(clean_text)
    with open(txt_filepath, 'w', encoding='utf-8') as f:
        f.write("\n".join(clean_lines))

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/info', methods=['POST'])
def get_metadata():
    url = request.json.get('url')
    if not url: return jsonify({"error": "No URL provided"}), 400

    ydl_opts = {'quiet': True, 'no_warnings': True,
        'remote_components': ['ejs:github'],
        'http_headers': {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'},
        'remote_components': ['ejs:github'],
        'http_headers': {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'} 'extract_flat': False}
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
        # Format parsing based on your PyQt code
        formats = info.get('formats', [])
        
        # Videos
        videos = [{"format_id": "bestvideo+bestaudio/best", "ext": "mp4", "resolution": "Best (Auto)", "vcodec": "auto", "filesize_mb": "Auto"}]
        raw_videos = [f for f in formats if f.get('vcodec') != 'none']
        raw_videos.sort(key=lambda x: (x.get('height', 0), x.get('tbr', 0)), reverse=True)
        for f in raw_videos:
            fs = f.get('filesize') or f.get('filesize_approx') or 0
            videos.append({
                "format_id": f.get('format_id'), "ext": f.get('ext', 'N/A'),
                "resolution": f.get('resolution') or f"{f.get('width', '')}x{f.get('height', '')}",
                "vcodec": f.get('vcodec', 'N/A'), "filesize_mb": f"{fs / (1024*1024):.1f} MB" if fs else "N/A"
            })

        # Audio
        audios = [{"format_id": "bestaudio/best", "ext": "mp3", "quality": "Best (Auto)", "acodec": "auto", "filesize_mb": "Auto"}]
        raw_audios = [f for f in formats if f.get('vcodec') == 'none' and f.get('acodec') != 'none']
        raw_audios.sort(key=lambda x: (x.get('abr', 0)), reverse=True)
        for f in raw_audios:
            fs = f.get('filesize') or f.get('filesize_approx') or 0
            audios.append({
                "format_id": f.get('format_id'), "ext": f.get('ext', 'N/A'),
                "quality": f"{f.get('abr', 0)} kbps" if f.get('abr') else "Audio",
                "acodec": f.get('acodec', 'N/A'), "filesize_mb": f"{fs / (1024*1024):.1f} MB" if fs else "N/A"
            })

        # Subs (Combined logic)
        subs = []
        manual = info.get('subtitles', {})
        for code, data in manual.items():
            subs.append({"name": data[0].get('name', code), "code": code, "category": "Manual"})
            
        auto = info.get('automatic_captions', {})
        for code, data in auto.items():
            if code not in manual:
                cat = f"Auto-gen ({code.split('-')[0]})" if code.split('-')[0] in PRIORITY_LANG_CODES else "Others"
                subs.append({"name": data[0].get('name', code), "code": code, "category": cat})

        return jsonify({
            "title": info.get('title', 'Unknown Title'),
            "safe_title": truncate_filename(info.get('title', 'video'), 3),
            "videos": videos, "audios": audios, "subs": subs
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/download', methods=['POST'])
def download():
    data = request.json
    url, fmt, dl_type = data.get('url'), data.get('format_id'), data.get('type')
    safe_title = data.get('safe_title', 'download')
    
    # We use a temporary directory so the server doesn't fill up with downloaded files
    temp_dir = tempfile.mkdtemp()
    base_path = os.path.join(temp_dir, safe_title)
    
    ydl_opts = {'quiet': True, 'outtmpl': f"{base_path}.%(ext)s"}

    try:
        if dl_type == 'video':
            ydl_opts.update({'format': fmt if "+" in fmt else f"{fmt}+bestaudio/best", 'merge_output_format': 'mp4'})
            expected_file = f"{base_path}.mp4"
            
        elif dl_type == 'audio':
            ydl_opts.update({
                'format': fmt,
                'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}]
            })
            expected_file = f"{base_path}.mp3"
            
        elif dl_type == 'sub':
            lang = data.get('lang')
            sub_format = data.get('sub_format', 'txt')
            fetch_fmt = 'vtt' if sub_format == 'txt' else sub_format
            ydl_opts.update({
                'skip_download': True, 'subtitlesformat': fetch_fmt, 'subtitleslangs': [lang],
                'writesubtitles': data.get('category') == 'Manual',
                'writeautomaticsub': data.get('category') != 'Manual'
            })
            expected_file = f"{base_path}.{lang}.{sub_format}"
            raw_vtt = f"{base_path}.{lang}.vtt"

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        if dl_type == 'sub' and sub_format == 'txt' and os.path.exists(raw_vtt):
            convert_vtt_to_txt(raw_vtt, expected_file)

        # Send the file to the user's browser, then schedule it for deletion
        @after_this_request
        def cleanup(response):
            try: os.remove(expected_file)
            except: pass
            return response

        return send_file(expected_file, as_attachment=True)
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
