import os
import sys
import requests
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from bs4 import XMLParsedAsHTMLWarning
import warnings
from pathlib import Path
import time
import subprocess
import configparser
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QLabel, QLineEdit, QPushButton,
                             QTextEdit, QProgressBar, QFileDialog, QCheckBox,
                             QSpinBox, QGroupBox, QMessageBox, QComboBox)
from PyQt5.QtCore import QThread, pyqtSignal, Qt
from PyQt5.QtGui import QFont


class ScraperThread(QThread):
    """Thread for scraping to keep GUI responsive"""
    progress = pyqtSignal(str)
    finished = pyqtSignal(int)
    error = pyqtSignal(str)

    def __init__(self, base_url, output_dir, options):
        super().__init__()
        self.base_url = base_url
        self.output_dir = output_dir
        self.options = options
        self.visited = set()
        self.is_running = True
        # Create a session with cookie support
        self.session = requests.Session()
        self.session.max_redirects = 10  # Reduce redirect limit

    def stop(self):
        self.is_running = False

    def normalize_url(self, url):
        """Normalize URL to avoid redirect loops with trailing slashes"""
        parsed = urlparse(url)
        # Remove fragment
        url_without_fragment = url.split('#')[0]
        # Normalize trailing slash for directories
        if not parsed.path or parsed.path == '/':
            return url_without_fragment
        # If path looks like a directory (no file extension), ensure trailing slash
        if '.' not in os.path.basename(parsed.path) and not parsed.path.endswith('/'):
            return url_without_fragment + '/'
        return url_without_fragment

    def is_valid_url(self, url):
        parsed = urlparse(url)
        base_parsed = urlparse(self.base_url)
        return parsed.netloc == base_parsed.netloc

    def get_relative_path(self, from_url, to_url):
        """Calculate relative path from one URL to another"""
        from_parsed = urlparse(from_url)
        to_parsed = urlparse(to_url)

        # Convert URLs to file paths
        from_path = from_parsed.path.strip('/')
        to_path = to_parsed.path.strip('/')

        # Normalize paths to include index.html
        if not from_path or from_path.endswith('/'):
            from_path = os.path.join(from_path, 'index.html')
        elif '.' not in os.path.basename(from_path):
            from_path = os.path.join(from_path, 'index.html')

        if not to_path or to_path.endswith('/'):
            to_path = os.path.join(to_path, 'index.html')
        elif '.' not in os.path.basename(to_path):
            to_path = os.path.join(to_path, 'index.html')

        # Calculate relative path
        from_dir = os.path.dirname(from_path)
        rel_path = os.path.relpath(to_path, from_dir)

        return rel_path.replace('\\', '/')  # Use forward slashes for web

    def is_xml_content(self, content, content_type):
        """Detect if content is XML"""
        # Check content-type header
        if content_type and ('xml' in content_type.lower() or 'application/rss' in content_type.lower()):
            return True

        # Check for XML declaration at start
        try:
            text = content.decode('utf-8', errors='ignore')[:200]
            if text.strip().startswith('<?xml'):
                return True
            # Check for common XML root elements
            if any(tag in text.lower() for tag in ['<rss', '<feed', '<urlset', '<svg']):
                return True
        except:
            pass

        return False

    def save_page(self, url, content, is_html=True):
        parsed = urlparse(url)
        path = parsed.path.strip('/')

        # Skip .git directories and files
        if '.git' in path.split('/'):
            return None

        if not path or path.endswith('/'):
            path = os.path.join(path, 'index.html')
        elif '.' not in os.path.basename(path):
            path = os.path.join(path, 'index.html')

        file_path = Path(self.output_dir) / path
        file_path.parent.mkdir(parents=True, exist_ok=True)

        # Convert links to relative if HTML
        if is_html:
            try:
                # Detect if this is actually XML
                content_type = 'text/html'  # Default assumption
                is_xml = self.is_xml_content(content, content_type)

                if is_xml:
                    self.progress.emit(f"ℹ️ Detected XML content: {url}")
                    # Try to use lxml for XML parsing
                    try:
                        soup = BeautifulSoup(content, 'lxml-xml')
                        self.progress.emit(f"✓ Parsed as XML using lxml")
                    except:
                        # Fall back to html.parser but filter the warning
                        warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
                        soup = BeautifulSoup(content, 'html.parser')
                        self.progress.emit(f"⚠️ Parsed as HTML (lxml not available for XML)")
                        warnings.resetwarnings()
                else:
                    soup = BeautifulSoup(content, 'html.parser')

                # Fix anchor links
                for link in soup.find_all('a', href=True):
                    href = link['href']
                    # Skip .git links
                    if '.git' in href:
                        continue
                    absolute_url = urljoin(url, href)
                    if self.is_valid_url(absolute_url):
                        link['href'] = self.get_relative_path(url, absolute_url)

                # Fix image sources
                for img in soup.find_all('img', src=True):
                    src = img['src']
                    if '.git' in src:
                        continue
                    absolute_url = urljoin(url, src)
                    if self.is_valid_url(absolute_url):
                        img['src'] = self.get_relative_path(url, absolute_url)

                # Fix CSS links
                for link in soup.find_all('link', href=True):
                    href = link['href']
                    if '.git' in href:
                        continue
                    absolute_url = urljoin(url, href)
                    if self.is_valid_url(absolute_url):
                        link['href'] = self.get_relative_path(url, absolute_url)

                # Fix script sources
                for script in soup.find_all('script', src=True):
                    src = script['src']
                    if '.git' in src:
                        continue
                    absolute_url = urljoin(url, src)
                    if self.is_valid_url(absolute_url):
                        script['src'] = self.get_relative_path(url, absolute_url)

                content = str(soup).encode('utf-8')
            except Exception as e:
                self.progress.emit(f"Warning: Could not fix links in {url}: {e}")

        with open(file_path, 'wb') as f:
            f.write(content)

        return file_path

    def scrape_page(self, url, depth=0):
        if not self.is_running:
            return

        # Normalize URL to avoid redirect loops
        url = self.normalize_url(url)

        if url in self.visited:
            return

        if depth > self.options['max_depth']:
            return

        # Skip .git URLs
        if '.git' in urlparse(url).path:
            return

        self.visited.add(url)
        self.progress.emit(f"Scraping: {url}")

        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1'
            }
            response = self.session.get(url, timeout=10, headers=headers)
            response.raise_for_status()

            # Check if content is HTML
            content_type = response.headers.get('content-type', '').lower()
            is_html = 'text/html' in content_type

            # Also detect XML even if content-type says HTML
            if is_html and self.is_xml_content(response.content, content_type):
                self.progress.emit(f"ℹ️ Content-Type is HTML but content appears to be XML")

            file_path = self.save_page(url, response.content, is_html=is_html)
            self.progress.emit(f"✓ Saved: {file_path}")

            # Parse for links - use appropriate parser
            if self.is_xml_content(response.content, content_type):
                try:
                    soup = BeautifulSoup(response.content, 'lxml-xml')
                except:
                    warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
                    soup = BeautifulSoup(response.content, 'html.parser')
                    warnings.resetwarnings()
            else:
                soup = BeautifulSoup(response.content, 'html.parser')

            # Find all links
            for link in soup.find_all('a', href=True):
                if not self.is_running:
                    return

                href = link['href']
                # Skip .git links
                if '.git' in href:
                    continue

                # Skip fragments and javascript links
                if href.startswith('#') or href.startswith('javascript:'):
                    continue

                absolute_url = urljoin(url, href)
                absolute_url = self.normalize_url(absolute_url)

                if self.is_valid_url(absolute_url) and absolute_url not in self.visited:
                    self.scrape_page(absolute_url, depth + 1)

            # Download resources if enabled
            if self.options['download_images']:
                for img in soup.find_all('img', src=True):
                    if not self.is_running:
                        return
                    src = img['src']
                    if '.git' in src:
                        continue
                    img_url = urljoin(url, src)
                    if self.is_valid_url(img_url):
                        self.download_resource(img_url)

            if self.options['download_css']:
                for link in soup.find_all('link', href=True, rel='stylesheet'):
                    if not self.is_running:
                        return
                    href = link['href']
                    if '.git' in href:
                        continue
                    css_url = urljoin(url, href)
                    if self.is_valid_url(css_url):
                        self.download_resource(css_url)

            if self.options['download_js']:
                for script in soup.find_all('script', src=True):
                    if not self.is_running:
                        return
                    src = script['src']
                    if '.git' in src:
                        continue
                    js_url = urljoin(url, src)
                    if self.is_valid_url(js_url):
                        self.download_resource(js_url)

            time.sleep(self.options['delay'])

        except requests.exceptions.TooManyRedirects:
            self.progress.emit(f"✗ Too many redirects: {url} (possible redirect loop)")
        except Exception as e:
            self.progress.emit(f"✗ Error: {url} - {str(e)}")

    def download_resource(self, url):
        if url in self.visited:
            return

        # Skip .git URLs
        if '.git' in urlparse(url).path:
            return

        # Normalize URL
        url = self.normalize_url(url)

        self.visited.add(url)

        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': '*/*',
                'Connection': 'keep-alive'
            }
            response = self.session.get(url, timeout=10, headers=headers)
            response.raise_for_status()
            file_path = self.save_page(url, response.content, is_html=False)
            if file_path:  # Only log if file was actually saved
                pass  # Silently save resources
        except requests.exceptions.TooManyRedirects:
            self.progress.emit(f"✗ Too many redirects for resource: {url}")
        except Exception as e:
            self.progress.emit(f"✗ Resource error: {url} - {str(e)}")

    def run(self):
        try:
            self.progress.emit(f"Starting scrape of {self.base_url}")
            self.scrape_page(self.base_url)

            if self.is_running:
                self.progress.emit(f"\n{'=' * 50}")
                self.progress.emit(f"Scraping complete!")
                self.progress.emit(f"Total URLs visited: {len(self.visited)}")
                self.progress.emit(f"Output directory: {self.output_dir}")
                self.finished.emit(len(self.visited))
            else:
                self.progress.emit("\nScraping stopped by user")
                self.finished.emit(0)

        except Exception as e:
            self.error.emit(str(e))


class GitThread(QThread):
    """Thread for Git operations"""
    progress = pyqtSignal(str)
    finished = pyqtSignal(bool)

    def __init__(self, repo_path, git_url, commit_message):
        super().__init__()
        self.repo_path = repo_path
        self.git_url = git_url
        self.commit_message = commit_message

    def run(self):
        try:
            os.chdir(self.repo_path)

            # Check if git repo exists
            if not os.path.exists('.git'):
                self.progress.emit("Initializing Git repository...")
                subprocess.run(['git', 'init'], check=True, capture_output=True)
                subprocess.run(['git', 'branch', '-M', 'main'], check=True, capture_output=True)

            self.progress.emit("Adding files...")
            subprocess.run(['git', 'add', '.'], check=True, capture_output=True)

            self.progress.emit("Creating commit...")
            subprocess.run(['git', 'commit', '-m', self.commit_message],
                           check=True, capture_output=True)

            # Check if remote exists
            result = subprocess.run(['git', 'remote'], capture_output=True, text=True)
            if 'origin' not in result.stdout:
                self.progress.emit("Adding remote...")
                subprocess.run(['git', 'remote', 'add', 'origin', self.git_url],
                               check=True, capture_output=True)

            self.progress.emit("Pushing to GitHub...")
            subprocess.run(['git', 'push', '-u', 'origin', 'main'],
                           check=True, capture_output=True)

            self.progress.emit("\n✓ Successfully pushed to GitHub!")
            self.finished.emit(True)

        except subprocess.CalledProcessError as e:
            self.progress.emit(f"\n✗ Git error: {e.stderr.decode() if e.stderr else str(e)}")
            self.finished.emit(False)
        except Exception as e:
            self.progress.emit(f"\n✗ Error: {str(e)}")
            self.finished.emit(False)


class WebScraperGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.scraper_thread = None
        self.git_thread = None
        self.history_file = 'scraper_history.ini'
        self.config = configparser.ConfigParser()
        self.load_history()
        self.init_ui()

    def load_history(self):
        """Load URL history from INI file"""
        if os.path.exists(self.history_file):
            self.config.read(self.history_file)
        else:
            self.config['URLs'] = {}

    def save_history(self, url):
        """Save URL to history INI file"""
        if not self.config.has_section('URLs'):
            self.config['URLs'] = {}

        # Get current timestamp
        timestamp = time.strftime('%Y-%m-%d %H:%M:%S')

        # Save URL with timestamp as key (to keep order and allow duplicates)
        key = f"url_{int(time.time())}_{len(self.config['URLs'])}"
        self.config['URLs'][key] = url

        # Write to file
        with open(self.history_file, 'w') as configfile:
            self.config.write(configfile)

    def get_history_urls(self):
        """Get list of unique URLs from history"""
        if not self.config.has_section('URLs'):
            return []

        # Get all URLs and remove duplicates while preserving order
        urls = list(self.config['URLs'].values())
        seen = set()
        unique_urls = []
        for url in reversed(urls):  # Reverse to get most recent first
            if url not in seen:
                seen.add(url)
                unique_urls.append(url)
        return unique_urls

    def clear_history(self):
        """Clear all URL history"""
        reply = QMessageBox.question(self, 'Clear History',
                                     'Are you sure you want to clear all URL history?',
                                     QMessageBox.Yes | QMessageBox.No,
                                     QMessageBox.No)

        if reply == QMessageBox.Yes:
            self.config['URLs'] = {}
            with open(self.history_file, 'w') as configfile:
                self.config.write(configfile)
            self.url_combo.clear()
            self.url_combo.setCurrentText('')
            QMessageBox.information(self, 'Success', 'URL history cleared!')

    def init_ui(self):
        self.setWindowTitle('Website Scraper - GitHub Integration')
        self.setGeometry(100, 100, 900, 700)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)

        # URL Input
        url_group = QGroupBox("Website to Scrape")
        url_layout = QVBoxLayout()

        url_input_layout = QHBoxLayout()
        url_input_layout.addWidget(QLabel('URL:'))

        # Replace QLineEdit with QComboBox for history dropdown
        self.url_combo = QComboBox()
        self.url_combo.setEditable(True)
        self.url_combo.setInsertPolicy(QComboBox.NoInsert)
        self.url_combo.lineEdit().setPlaceholderText('https://example.com')

        # Populate with history
        history_urls = self.get_history_urls()
        if history_urls:
            self.url_combo.addItems(history_urls)
            self.url_combo.setCurrentText('')

        url_input_layout.addWidget(self.url_combo)

        # Clear history button
        self.clear_history_btn = QPushButton('Clear History')
        self.clear_history_btn.clicked.connect(self.clear_history)
        self.clear_history_btn.setMaximumWidth(120)
        url_input_layout.addWidget(self.clear_history_btn)

        url_layout.addLayout(url_input_layout)

        url_group.setLayout(url_layout)
        layout.addWidget(url_group)

        # Output Directory
        output_group = QGroupBox("Output Settings")
        output_layout = QVBoxLayout()

        dir_layout = QHBoxLayout()
        dir_layout.addWidget(QLabel('Output Dir:'))
        self.output_dir = QLineEdit('scraped_site')
        dir_layout.addWidget(self.output_dir)
        self.browse_btn = QPushButton('Browse')
        self.browse_btn.clicked.connect(self.browse_directory)
        dir_layout.addWidget(self.browse_btn)
        output_layout.addLayout(dir_layout)

        output_group.setLayout(output_layout)
        layout.addWidget(output_group)

        # Scraper Options
        options_group = QGroupBox("Scraper Options")
        options_layout = QVBoxLayout()

        self.download_images = QCheckBox('Download Images')
        self.download_images.setChecked(True)
        options_layout.addWidget(self.download_images)

        self.download_css = QCheckBox('Download CSS')
        self.download_css.setChecked(True)
        options_layout.addWidget(self.download_css)

        self.download_js = QCheckBox('Download JavaScript')
        self.download_js.setChecked(True)
        options_layout.addWidget(self.download_js)

        depth_layout = QHBoxLayout()
        depth_layout.addWidget(QLabel('Max Depth:'))
        self.max_depth = QSpinBox()
        self.max_depth.setMinimum(1)
        self.max_depth.setMaximum(10)
        self.max_depth.setValue(3)
        depth_layout.addWidget(self.max_depth)
        depth_layout.addStretch()
        options_layout.addLayout(depth_layout)

        delay_layout = QHBoxLayout()
        delay_layout.addWidget(QLabel('Delay (seconds):'))
        self.delay = QSpinBox()
        self.delay.setMinimum(0)
        self.delay.setMaximum(10)
        self.delay.setValue(1)
        delay_layout.addWidget(self.delay)
        delay_layout.addStretch()
        options_layout.addLayout(delay_layout)

        options_group.setLayout(options_layout)
        layout.addWidget(options_group)

        # Git Settings
        git_group = QGroupBox("GitHub Settings")
        git_layout = QVBoxLayout()

        git_url_layout = QHBoxLayout()
        git_url_layout.addWidget(QLabel('Repo URL:'))
        self.git_url = QLineEdit()
        self.git_url.setPlaceholderText('https://github.com/username/repo.git')
        git_url_layout.addWidget(self.git_url)
        git_layout.addLayout(git_url_layout)

        commit_layout = QHBoxLayout()
        commit_layout.addWidget(QLabel('Commit Message:'))
        self.commit_message = QLineEdit('Update scraped content')
        commit_layout.addWidget(self.commit_message)
        git_layout.addLayout(commit_layout)

        git_group.setLayout(git_layout)
        layout.addWidget(git_group)

        # Buttons
        button_layout = QHBoxLayout()
        self.start_btn = QPushButton('Start Scraping')
        self.start_btn.clicked.connect(self.start_scraping)
        button_layout.addWidget(self.start_btn)

        self.stop_btn = QPushButton('Stop')
        self.stop_btn.clicked.connect(self.stop_scraping)
        self.stop_btn.setEnabled(False)
        button_layout.addWidget(self.stop_btn)

        self.push_btn = QPushButton('Push to GitHub')
        self.push_btn.clicked.connect(self.push_to_github)
        self.push_btn.setEnabled(False)
        button_layout.addWidget(self.push_btn)

        self.save_log_btn = QPushButton('Save Log')
        self.save_log_btn.clicked.connect(self.save_log)
        button_layout.addWidget(self.save_log_btn)

        layout.addLayout(button_layout)

        # Log Output
        layout.addWidget(QLabel('Progress Log:'))
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setFont(QFont('Courier', 9))
        layout.addWidget(self.log_output)

    def browse_directory(self):
        directory = QFileDialog.getExistingDirectory(self, "Select Output Directory")
        if directory:
            self.output_dir.setText(directory)

    def start_scraping(self):
        url = self.url_combo.currentText().strip()
        output_dir = self.output_dir.text().strip()

        if not url:
            QMessageBox.warning(self, 'Error', 'Please enter a URL')
            return

        if not output_dir:
            QMessageBox.warning(self, 'Error', 'Please specify an output directory')
            return

        # Save URL to history
        self.save_history(url)

        # Update combobox with new history
        self.url_combo.clear()
        history_urls = self.get_history_urls()
        if history_urls:
            self.url_combo.addItems(history_urls)
        self.url_combo.setCurrentText(url)

        self.log_output.clear()
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.push_btn.setEnabled(False)

        options = {
            'download_images': self.download_images.isChecked(),
            'download_css': self.download_css.isChecked(),
            'download_js': self.download_js.isChecked(),
            'max_depth': self.max_depth.value(),
            'delay': self.delay.value()
        }

        self.scraper_thread = ScraperThread(url, output_dir, options)
        self.scraper_thread.progress.connect(self.update_log)
        self.scraper_thread.finished.connect(self.scraping_finished)
        self.scraper_thread.error.connect(self.scraping_error)
        self.scraper_thread.start()

    def stop_scraping(self):
        if self.scraper_thread:
            self.scraper_thread.stop()
            self.stop_btn.setEnabled(False)

    def scraping_finished(self, count):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)

        if count > 0:
            self.push_btn.setEnabled(True)

    def scraping_error(self, error):
        self.log_output.append(f"\n✗ Error: {error}")
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)

    def push_to_github(self):
        git_url = self.git_url.text().strip()
        output_dir = self.output_dir.text().strip()
        commit_msg = self.commit_message.text().strip()

        if not git_url:
            QMessageBox.warning(self, 'Error', 'Please enter a GitHub repository URL')
            return

        if not os.path.exists(output_dir):
            QMessageBox.warning(self, 'Error', 'Output directory does not exist')
            return

        self.push_btn.setEnabled(False)
        self.log_output.append(f"\n{'=' * 50}\nStarting Git operations...\n")

        self.git_thread = GitThread(output_dir, git_url, commit_msg)
        self.git_thread.progress.connect(self.update_log)
        self.git_thread.finished.connect(self.git_finished)
        self.git_thread.start()

    def git_finished(self, success):
        self.push_btn.setEnabled(True)
        if success:
            QMessageBox.information(self, 'Success', 'Successfully pushed to GitHub!')

    def save_log(self):
        """Save log output to a file"""
        if not self.log_output.toPlainText():
            QMessageBox.warning(self, 'Error', 'Log is empty. Nothing to save.')
            return

        # Generate default filename with timestamp
        timestamp = time.strftime('%Y%m%d_%H%M%S')
        default_filename = f'scraper_log_{timestamp}.txt'

        # Open file dialog
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            'Save Log File',
            default_filename,
            'Text Files (*.txt);;Log Files (*.log);;All Files (*)'
        )

        if file_path:
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(self.log_output.toPlainText())
                QMessageBox.information(self, 'Success', f'Log saved to:\n{file_path}')
            except Exception as e:
                QMessageBox.critical(self, 'Error', f'Failed to save log:\n{str(e)}')

    def update_log(self, message):
        self.log_output.append(message)
        self.log_output.verticalScrollBar().setValue(
            self.log_output.verticalScrollBar().maximum()
        )


if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = WebScraperGUI()
    window.show()
    sys.exit(app.exec_())