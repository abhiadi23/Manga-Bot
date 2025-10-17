import os
import time
import json
import subprocess
import requests
from pathlib import Path
from datetime import datetime, timedelta
from telegram import Bot
from telegram.error import TelegramError
import asyncio

class MangaDexScraperBot:
    def __init__(self, telegram_token, channel_id, manga_ids, check_interval=300):
        """
        Initialize the MangaDex scraper bot
        
        Args:
            telegram_token: Telegram bot token
            channel_id: Telegram channel ID (e.g., @channelname or -100123456789)
            manga_ids: List of MangaDex manga IDs to monitor
            check_interval: Time in seconds between checks (default: 300 = 5 minutes)
        """
        self.telegram_token = telegram_token
        self.channel_id = channel_id
        self.manga_ids = manga_ids if isinstance(manga_ids, list) else [manga_ids]
        self.check_interval = check_interval
        
        # MangaDex API settings
        self.api_base = "https://api.mangadex.org"
        self.img_base = "https://uploads.mangadex.org"
        
        # Directories
        self.download_dir = Path("downloads")
        self.state_file = Path("bot_state.json")
        self.download_dir.mkdir(exist_ok=True)
        
        # Load or initialize state
        self.state = self.load_state()
        
        # Rate limiting
        self.last_request_time = 0
        self.min_request_interval = 0.25  # 250ms between requests (MangaDex rate limit)
        
        # Channel message cache
        self.channel_messages_cache = {}
        self.cache_last_updated = {}
        
    def load_state(self):
        """Load the bot state from file"""
        if self.state_file.exists():
            with open(self.state_file, 'r') as f:
                return json.load(f)
        return {"last_checked": {}, "downloaded_chapters": {}}
    
    def save_state(self):
        """Save the bot state to file"""
        with open(self.state_file, 'w') as f:
            json.dump(self.state, f, indent=2)
    
    def rate_limit(self):
        """Implement rate limiting for API requests"""
        elapsed = time.time() - self.last_request_time
        if elapsed < self.min_request_interval:
            time.sleep(self.min_request_interval - elapsed)
        self.last_request_time = time.time()
    
    def api_request(self, endpoint, params=None):
        """Make a rate-limited API request to MangaDex"""
        self.rate_limit()
        url = f"{self.api_base}{endpoint}"
        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"API request failed: {e}")
            return None
    
    def get_manga_info(self, manga_id):
        """Get manga information"""
        data = self.api_request(f"/manga/{manga_id}")
        if data and data.get("result") == "ok":
            attributes = data["data"]["attributes"]
            title = attributes["title"].get("en", list(attributes["title"].values())[0])
            return {"id": manga_id, "title": title}
        return None
    
    def get_new_chapters(self, manga_id, since=None):
        """Get new chapters for a manga"""
        params = {
            "manga": manga_id,
            "translatedLanguage[]": ["en"],
            "order[chapter]": "desc",
            "limit": 100,
            "contentRating[]": ["safe", "suggestive", "erotica", "pornographic"]
        }
        
        if since:
            params["publishAtSince"] = since
        
        data = self.api_request("/chapter", params=params)
        
        if not data or data.get("result") != "ok":
            return []
        
        chapters = []
        for item in data["data"]:
            chapter_id = item["id"]
            attrs = item["attributes"]
            
            # Skip external chapters
            if attrs.get("externalUrl"):
                continue
            
            # Get scanlation group
            group_name = "Unknown"
            for rel in item.get("relationships", []):
                if rel["type"] == "scanlation_group":
                    group_name = rel.get("attributes", {}).get("name", "Unknown")
                    break
            
            chapters.append({
                "id": chapter_id,
                "chapter": attrs.get("chapter", "0"),
                "title": attrs.get("title", ""),
                "pages": attrs.get("pages", 0),
                "publish_at": attrs.get("publishAt"),
                "group": group_name
            })
        
        return chapters
    
    def get_chapter_images(self, chapter_id):
        """Get image URLs for a chapter"""
        # Get chapter info with image data
        data = self.api_request(f"/at-home/server/{chapter_id}")
        
        if not data or data.get("result") != "ok":
            return None
        
        base_url = data["baseUrl"]
        chapter_hash = data["chapter"]["hash"]
        filenames = data["chapter"]["data"]  # High quality images
        
        images = []
        for filename in filenames:
            url = f"{base_url}/data/{chapter_hash}/{filename}"
            images.append(url)
        
        return images
    
    def download_with_aria2c(self, urls, output_dir):
        """Download images using aria2c"""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Create input file for aria2c
        input_file = output_dir / "aria2c_input.txt"
        with open(input_file, 'w') as f:
            for i, url in enumerate(urls):
                f.write(f"{url}\n")
                f.write(f"  out={i+1:03d}.jpg\n")
        
        # Run aria2c
        cmd = [
            "aria2c",
            "-i", str(input_file),
            "-d", str(output_dir),
            "-x", "16",  # Max connections per file
            "-s", "16",  # Split download
            "-j", "5",   # Max concurrent downloads
            "--auto-file-renaming=false",
            "--allow-overwrite=true"
        ]
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"aria2c error: {result.stderr}")
                return False
            
            # Clean up input file
            input_file.unlink()
            return True
        except Exception as e:
            print(f"Download failed: {e}")
            return False
    
    def create_chapter_archive(self, chapter_dir, manga_title, chapter_num, chapter_title):
        """Create a CBZ archive from downloaded images"""
        chapter_name = f"{manga_title} - Chapter {chapter_num}"
        if chapter_title:
            chapter_name += f" - {chapter_title}"
        
        # Sanitize filename
        chapter_name = "".join(c for c in chapter_name if c.isalnum() or c in (' ', '-', '_', '.'))
        archive_path = chapter_dir.parent / f"{chapter_name}.cbz"
        
        # Create CBZ (ZIP with .cbz extension)
        import zipfile
        with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for img_file in sorted(chapter_dir.glob("*.jpg")):
                zipf.write(img_file, img_file.name)
        
        return archive_path
    
    async def upload_to_telegram(self, file_path, caption):
        """Upload file to Telegram channel"""
        bot = Bot(token=self.telegram_token)
        
        try:
            with open(file_path, 'rb') as f:
                await bot.send_document(
                    chat_id=self.channel_id,
                    document=f,
                    caption=caption,
                    filename=file_path.name
                )
            return True
        except TelegramError as e:
            print(f"Telegram upload failed: {e}")
            return False
    
    def process_chapter(self, manga_info, chapter):
        """Download and upload a single chapter"""
        manga_title = manga_info["title"]
        chapter_id = chapter["id"]
        chapter_num = chapter["chapter"]
        chapter_title = chapter["title"]
        
        print(f"Processing: {manga_title} - Chapter {chapter_num}")
        
        # Check if already downloaded
        if chapter_id in self.state["downloaded_chapters"].get(manga_info["id"], []):
            print(f"  Already downloaded, skipping...")
            return
        
        # Get image URLs
        print(f"Getting image URLs...")
        image_urls = self.get_chapter_images(chapter_id)
        if not image_urls:
            print(f"Failed to get image URLs")
            return
        
        print(f"Found {len(image_urls)} pages")
        
        # Download images
        chapter_dir = self.download_dir / f"{manga_info['id']}" / f"chapter_{chapter_num}"
        print(f"Downloading with aria2c...")
        if not self.download_with_aria2c(image_urls, chapter_dir):
            print(f"  Download failed")
            return
        
        # Create archive
        print(f"Creating CBZ archive...")
        archive_path = self.create_chapter_archive(
            chapter_dir, manga_title, chapter_num, chapter_title
        )
        
        # Upload to Telegram
        caption = f"{manga_title} Ch-{chapter_num}\n @seishiro_atanime"
        if chapter_title:
            caption += f"\n{caption}"
        
        print(f"  Uploading to Telegram...")
        asyncio.run(self.upload_to_telegram(archive_path, caption))
        
        # Update state
        if manga_info["id"] not in self.state["downloaded_chapters"]:
        self.state["downloaded_chapters"][manga_info["id"]] = []
        self.state["downloaded_chapters"][manga_info["id"]].append(chapter_id)
        self.save_state()
        
        print(f"✓ Complete!")
    
    def check_for_updates(self):
        """Check all manga for new chapters"""
        print(f"\n{'='*60}")
        print(f"Checking for updates - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*60}")
        
        for manga_id in self.manga_ids:
            print(f"\nChecking manga: {manga_id}")
            
            # Get manga info
            manga_info = self.get_manga_info(manga_id)
            if not manga_info:
                print(f"  Failed to get manga info")
                continue
            
            print(f"Title: {manga_info['title']}")
            
            # Get last check time
            last_checked = self.state["last_checked"].get(manga_id)
            since_date = None
            if last_checked:
                since_date = (datetime.fromisoformat(last_checked) - timedelta(hours=1)).isoformat()
            
            # Get new chapters
            chapters = self.get_new_chapters(manga_id, since=since_date)
            print(f"  Found {len(chapters)} chapters")
            
            # Filter out already downloaded chapters
            new_chapters = [
                ch for ch in chapters
                if ch["id"] not in self.state["downloaded_chapters"].get(manga_id, [])
            ]
            
            if new_chapters:
                print(f"New chapters to download: {len(new_chapters)}")
                # Process chapters in order (oldest first)
                for chapter in reversed(new_chapters):
                    self.process_chapter(manga_info, chapter)
                    time.sleep(2)  # Small delay between chapters
            else:
                print(f"No new chapters")
            
            # Update last checked time
            self.state["last_checked"][manga_id] = datetime.now().isoformat()
            self.save_state()
    
    def run(self):
        """Main bot loop"""
        print("="*60)
        print("MangaDex Auto Scraper Bot Started")
        print("="*60)
        print(f"Monitoring {len(self.manga_ids)} manga")
        print(f"Check interval: {self.check_interval} seconds")
        print(f"Telegram channel: {self.channel_id}")
        print("="*60)
        
        while True:
            try:
                self.check_for_updates()
                print(f"\nNext check in {self.check_interval} seconds...")
                time.sleep(self.check_interval)
            except KeyboardInterrupt:
                print("\n\nBot stopped by user")
                break
            except Exception as e:
                print(f"\nError in main loop: {e}")
                print(f"Retrying in {self.check_interval} seconds...")
                time.sleep(self.check_interval)

# Example usage
if __name__ == "__main__":
    # List of manga IDs to monitor (get from MangaDex URL)
    # Example: https://mangadex.org/title/MANGA_ID/manga-name
    MANGA_IDS = [
        "MANGA_ID_1",
        "MANGA_ID_2",
        # Add more manga IDs here
    ]
    
    # Check every 5 minutes (300 seconds)
    CHECK_INTERVAL = 300
    
    # Create and run bot
    bot = MangaDexScraperBot(
        telegram_token=Config.BOT_TOKEN,
        channel_id=Config.CHANNEL_ID,
        manga_ids=MANGA_IDS,
        check_interval=CHECK_INTERVAL
    )
    
    bot.run()
