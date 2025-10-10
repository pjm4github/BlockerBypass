import os
import requests
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from pathlib import Path
import time


class WebsiteScraper:
    def __init__(self, base_url, output_dir='scraped_site'):
        self.base_url = base_url
        self.domain = urlparse(base_url).netloc
        self.output_dir = Path(output_dir)
        self.visited = set()
        self.output_dir.mkdir(exist_ok=True)

    def is_valid_url(self, url):
        """Check if URL belongs to the same domain"""
        parsed = urlparse(url)
        return parsed.netloc == self.domain

    def save_page(self, url, content):
        """Save page content to file"""
        parsed = urlparse(url)
        path = parsed.path.strip('/')

        # Create directory structure
        if not path or path.endswith('/'):
            path = os.path.join(path, 'index.html')
        elif '.' not in os.path.basename(path):
            path = os.path.join(path, 'index.html')

        file_path = self.output_dir / path
        file_path.parent.mkdir(parents=True, exist_ok=True)

        with open(file_path, 'wb') as f:
            f.write(content)
        print(f"Saved: {file_path}")

    def scrape_page(self, url):
        """Scrape a single page and find links"""
        if url in self.visited:
            return

        self.visited.add(url)

        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()

            # Save the page
            self.save_page(url, response.content)

            # Parse HTML for links
            soup = BeautifulSoup(response.content, 'html.parser')

            # Find all links
            for link in soup.find_all('a', href=True):
                href = link['href']
                absolute_url = urljoin(url, href)

                if self.is_valid_url(absolute_url):
                    self.scrape_page(absolute_url)

            # Optional: Save images, CSS, JS
            for img in soup.find_all('img', src=True):
                img_url = urljoin(url, img['src'])
                if self.is_valid_url(img_url):
                    self.download_resource(img_url)

            time.sleep(0.5)  # Be polite, don't hammer the server

        except Exception as e:
            print(f"Error scraping {url}: {e}")

    def download_resource(self, url):
        """Download images, CSS, JS, etc."""
        if url in self.visited:
            return
        self.visited.add(url)

        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            self.save_page(url, response.content)
        except Exception as e:
            print(f"Error downloading {url}: {e}")

    def scrape(self):
        """Start scraping from base URL"""
        print(f"Starting scrape of {self.base_url}")
        self.scrape_page(self.base_url)
        print(f"Scraping complete. Visited {len(self.visited)} URLs")


# Usage
if __name__ == "__main__":
    scraper = WebsiteScraper('https://dpsim.fein-aachen.org/', 'scraped_site')
    scraper.scrape()