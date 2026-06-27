import os
import yt_dlp
from pydub import AudioSegment

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)


def download_youtube_audio(url: str) -> str:
    output_path = os.path.join(DOWNLOAD_DIR, "%(title)s.%(ext)s")

    browsers = ["chrome", "safari", "firefox"]

    last_error = None

    for browser in browsers:
        try:
            print(f"Trying YouTube download using {browser} cookies...")

            ydl_opts = {
                "format": "bestaudio/best",
                "outtmpl": output_path,
                "cookiesfrombrowser": (browser,),
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "wav",
                        "preferredquality": "192",
                    }
                ],
                "quiet": False,
                "noplaylist": True,
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)

                downloaded_file = ydl.prepare_filename(info)

                wav_file = os.path.splitext(downloaded_file)[0] + ".wav"

                if os.path.exists(wav_file):
                    return wav_file

                return downloaded_file

        except Exception as e:
            print(f"{browser} failed: {e}")
            last_error = e

    raise RuntimeError(
        f"""
Unable to download YouTube audio.

Possible reasons:
1. You are not logged into YouTube in Chrome/Safari/Firefox.
2. YouTube is blocking automated requests.
3. Video is age-restricted/private.
4. Browser cookies could not be accessed.

Original error:
{last_error}
"""
    )


def convert_to_wav(input_path: str) -> str:
    """Convert any audio/video file to WAV format."""
    output_path = os.path.splitext(input_path)[0] + "_converted.wav"

    audio = AudioSegment.from_file(input_path)
    audio = audio.set_channels(1).set_frame_rate(16000)

    audio.export(output_path, format="wav")

    return output_path


def chunk_audio(wav_path: str, chunk_minutes: int = 10) -> list:
    audio = AudioSegment.from_wav(wav_path)

    chunk_ms = chunk_minutes * 60 * 1000

    chunks = []

    for i, start in enumerate(range(0, len(audio), chunk_ms)):
        chunk = audio[start:start + chunk_ms]

        chunk_path = f"{wav_path}_chunk_{i}.wav"

        chunk.export(chunk_path, format="wav")

        chunks.append(chunk_path)

    return chunks


def process_input(source: str) -> list:
    if source.startswith("http://") or source.startswith("https://"):
        print("Detected YouTube URL. Downloading audio...")
        wav_path = download_youtube_audio(source)
    else:
        print("Detected local file. Converting to WAV...")
        wav_path = convert_to_wav(source)

    print("Chunking audio...")

    chunks = chunk_audio(wav_path)

    print(f"Audio ready — {len(chunks)} chunk(s) created.")

    return chunks