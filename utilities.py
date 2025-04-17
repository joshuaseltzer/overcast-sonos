"""
Utility functions.

Little utility functions to help you along :)
"""

import requests
import logging
import re
import os
import shutil
import time
import unicodedata
import urllib.parse
from pathlib import Path
from datetime import datetime


log = logging.getLogger('overcast-sonos')


INVALID_PODCAST_HOSTS = ['megaphone.fm', 'podtoo.com', 'podbean.com']
DOWNLOAD_CHUNK_SIZE = 256 * 1024   # 256 KB
USER_AGENT = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15'


# Turns a string like 'Feb 24 - 36 min left' into seconds
def duration_in_seconds(duration_str):
    seconds = -1
    try:
        strings = duration_str.split()
        if 'at' in strings:
            log.debug("Duration could not be determined because Overcast is giving the start time instead of time left")
            return seconds
        else:
            minuteIndex = strings.index('min') - 1
            seconds = int(strings[minuteIndex]) * 60
            log.debug(f"Parsed the episode's duration in seconds from the string {duration_str} -> {seconds}")
            return seconds
    except:
        log.debug(f"Couldn't parse the episode's duration in seconds from the string {duration_str}")
        return seconds


# Works out the final URL for those podcast platforms that redirect to another URL
# If the redirected URL has a #t= timecode in it, we remove this as the Sonos player can't play these back, and it fixes compatibility with requests 2.19 and higher
def final_redirect_url(url, title, podcast_title, local_ip, local_port, local_download_dir):
    # in a previous version of this logic, a HEAD request was used but some servers did not redirect properly unless a GET was sent
    with requests.get(url, allow_redirects=False, stream=True, headers={'User-Agent': USER_AGENT}) as response:
        if response.is_redirect:
            redirected_url = response.headers['Location']
            log.info(f"Redirected {url} to {redirected_url}")
            return final_redirect_url(redirected_url, title, podcast_title, local_ip, local_port, local_download_dir)
        elif response.ok:
            log.info(f"Final URL for this podcast: {url}")

            # for certain podcasts, the '#=' is added to the audio URL which causes Sonos to fail to connect
            regex='#t=[0-9]*$'
            if re.search(regex, url):
                log.info("Truncating the #t= part of the URL")
                url = re.sub(regex, '', url)

            # Check the hostname for any invalid hosts (i.e. ones that do not play correctly on Sonos).
            # If an invalid host is found and local server information is provided, download the file directly to the system
            # and then return that URL for Sonos to stream from.
            parsed_url = urllib.parse.urlparse(url)
            if any(invalid_host in parsed_url.hostname for invalid_host in INVALID_PODCAST_HOSTS):
                log.info(f"\"{podcast_title}\" is hosted from a URL which will refuse to play on Sonos ({parsed_url.hostname})")

                # if local parameters were given, attempt to download and host the podcast directly from this server
                file_ext = os.path.splitext(parsed_url.path)[1]
                if file_ext != '' and local_ip and local_port and local_download_dir:
                    # get a valid file path for the file as it will be downloaded locally to this server
                    podcast_dir = slugify(podcast_title)
                    filename = f'{slugify(title)}{file_ext}'
                    file_dir = os.path.join(local_download_dir, podcast_dir)
                    full_file_path = os.path.join(file_dir, filename)
                    if not os.path.exists(full_file_path):
                        # since the file does not exist locally, download it now
                        os.makedirs(file_dir, exist_ok=True)
                        log.info(f"Downloading podcast to {full_file_path} from {url}")
                        with open(full_file_path, 'wb') as f:
                            shutil.copyfileobj(response.raw, f, length=DOWNLOAD_CHUNK_SIZE)

                    # create the URL that will be used to host this podcast file
                    url = f"http://{local_ip}:{local_port}/{podcast_dir}/{filename}"
            return url
        else:
            log.error(f"Error trying to determine the final URL for podcast: {response}")
            return ""


# Turns a string like 'Dec 2, 2020 • 171 min' or 'Jan 13 • 147 min' into a date
# Sets a default date in case it can't parse it of 2000-01-01
def convert_release_date(str):
    final_date = '2000-01-01T00:00:00'
    try:
        strings = str.split('•')
        strdate = strings[0]
        strdate = strdate.replace(' ','')
        strdate = strdate.replace('\n','')
        if ',' in strdate:
            date = datetime.strptime(strdate, '%b%d,%Y')
            final_date = date.isoformat()
        else:
            thisyear = datetime.now().year
            date = datetime.strptime(strdate, '%b%d')
            date = date.replace(year=thisyear)
            final_date = date.isoformat()
    except:
        pass

    return final_date


# cleans up the given directory by removing any files older than the specified days
def cleanup_directory(directory, keep_for_days):
    log.info(f"Cleaning directory \"{directory}\" by deleting files over {keep_for_days} days old")

    cutoff_time = time.time() - (keep_for_days * 86400)
    for file in list(Path(directory).rglob("*")):
        if not file.is_file():
            continue

        if os.path.getmtime(file) < cutoff_time:
            log.info(f"Deleting old file: {file}")
            try:
                file.unlink()

                # attempt to delete the parent directory if the directory is now empty
                if file.parent.is_dir() and len(os.listdir(file.parent)) == 0:
                    file.parent.rmdir()
                    log.info(f"Removing empty directory: {file.parent}")
            except Exception:
                log.error(f"Could not delete file: {file}")


# Taken from https://github.com/django/django/blob/main/django/utils/text.py
def slugify(value, allow_unicode=False):
    """
    Convert to ASCII if 'allow_unicode' is False. Convert spaces or repeated
    dashes to single dashes. Remove characters that aren't alphanumerics,
    underscores, or hyphens. Convert to lowercase. Also strip leading and
    trailing whitespace, dashes, and underscores.
    """
    value = str(value)
    if allow_unicode:
        value = unicodedata.normalize("NFKC", value)
    else:
        value = (
            unicodedata.normalize("NFKD", value)
            .encode("ascii", "ignore")
            .decode("ascii")
        )
    value = re.sub(r"[^\w\s-]", "", value.lower())
    return re.sub(r"[-\s]+", "-", value).strip("-_")
