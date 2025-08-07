"""
Utility functions.

Little utility functions to help you along :)
"""

import requests
import logging
import glob
import re
import os
import shutil
import time
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path
from datetime import datetime


log = logging.getLogger('overcast-sonos')


INVALID_PODCAST_HOSTS = ['megaphone.fm', 'podtoo.com', 'podbean.com']
DOWNLOAD_CHUNK_SIZE = 2 * 1024 * 1024   # 1 MB
USER_AGENT = 'Overcast/3.0 (+http://overcast.fm/; iOS podcast app)'


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


# Download all of the bytes for podcasts which return partial content (HTTP status 206) and provide a content-range.
# Returns true on success or false if any error occurs.
def write_bytes(url, initial_response, full_file_path):
    success = True

    try:
        # check if we might potentially need to request multiple ranges
        with open(full_file_path, 'wb') as file:
            if initial_response.status_code == 206 and initial_response.headers.get('accept-ranges', '') == 'bytes':
                # determine the full length of the content
                log.info('Response returned Partial Content (206) and supports Accept-Ranges')
                content_range = initial_response.headers.get("content-range")
                if content_range:
                    total_size = content_range.split('/')[-1]
                    if total_size != '*' and total_size.isdigit():
                        total_size = int(total_size)
                        content_length = initial_response.headers.get("content-length")
                        if content_length and content_length.isdigit() and int(content_length) == total_size:
                            # if the response's content length is equal to the total size, fall back to getting the bytes using iter_content
                            log.info('The Content-Length is the same as the total size reported by Content-Range and therefore will be downloaded in a single request')
                            for chunk in initial_response.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                                if chunk:
                                    file.write(chunk)
                        else:
                            # write the initial chunk of data
                            bytes_length = len(initial_response.content)
                            file.write(initial_response.content)
                            log.info(f"Saved {bytes_length}")
                            while bytes_length < total_size:
                                # keep requesting content as long as some remains
                                with requests.get(url, stream=True, headers={'Range': f'bytes={bytes_length}-', 'User-Agent': USER_AGENT}) as chunk_response:
                                    if chunk_response.ok:
                                        bytes_length += len(chunk_response.content)
                                        file.write(chunk_response.content)
                                        log.info(f"Saved {bytes_length}")
                                    else:
                                        log.error(f"An error occurred when downloading a chunked Range request")
                                        success = False
                                        break
                    else:
                        log.error(f"The total file size could not be determined for Range requests")
                        success = False
                else:
                    log.error(f"The Content-Range header was not included which is required for Range requests")
                    success = False
            else:
                log.info('Response will be downloaded in a single request')
                for chunk in initial_response.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                    if chunk:
                        file.write(chunk)
    except Exception as e:
        log.error(f"An unhandled exception occurred while writing the bytes: {e}")
        success = False

    return success


# Works out the final URL for those podcast platforms that redirect to another URL
# If the redirected URL has a #t= timecode in it, we remove this as the Sonos player can't play these back, and it fixes compatibility with requests 2.19 and higher
def final_redirect_url(url, title, podcast_title, local_ip, local_port, local_download_dir, check_for_local_download=True):
    # before making the first redirect request, check to see if a podcast file already exists locally on the server
    if check_for_local_download and local_ip and local_port and local_download_dir:
        podcast_dir = slugify(podcast_title)
        full_podcast_dir = os.path.join(local_download_dir, podcast_dir)
        filename = slugify(title)

        # since we don't know the file extension for the podcast, use a wildcard to match any podcast with the title and use the first result
        local_podcast_files = glob.glob(os.path.join(full_podcast_dir, filename) + ".*")
        if len(local_podcast_files) > 0:
            full_filename = os.path.basename(local_podcast_files[0])
            log.info(f"A copy of \"{title}\" has already been downloaded to the server ({full_filename})")
            url = f"http://{local_ip}:{local_port}/{podcast_dir}/{full_filename}"
            log.info(f"Using a locally-hosted URL for this podcast: {url}")
            return url

    # in a previous version of this logic, a HEAD request was used but some servers did not redirect properly unless a GET was sent
    with requests.get(url, allow_redirects=False, stream=True, headers={'Range': 'bytes=0-', 'User-Agent': USER_AGENT}) as response:
        if response.is_redirect:
            redirected_url = response.headers['Location']
            log.info(f"Redirected {url} to {redirected_url}")
            return final_redirect_url(redirected_url, title, podcast_title, local_ip, local_port, local_download_dir, False)
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
                log.info(f"\"{title}\" is hosted from a URL which will refuse to play on Sonos ({parsed_url.hostname})")

                # if local parameters were given, attempt to download and host the podcast directly from this server
                file_ext = os.path.splitext(parsed_url.path)[1]
                if file_ext != '' and local_ip and local_port and local_download_dir:
                    # get a valid file path for the file as it will be downloaded locally to this server
                    if not check_for_local_download:
                        podcast_dir = slugify(podcast_title)
                        full_podcast_dir = os.path.join(local_download_dir, podcast_dir)
                        filename = slugify(title)
                        
                    full_filename = f'{filename}{file_ext}'
                    full_file_path = os.path.join(full_podcast_dir, full_filename)
                    if not os.path.exists(full_file_path):
                        # since the file does not exist locally, download it now
                        os.makedirs(full_podcast_dir, exist_ok=True)
                        log.info(f"Downloading podcast to {full_file_path} from {url}")
                        if write_bytes(url, response, full_file_path):
                            log.info(f"Successfully downloaded {full_filename}")
                        else:
                            log.error(f"Error downloading {full_filename}")
                            os.remove(full_file_path)
                            return ""

                    # create the URL that will be used to host this podcast file
                    url = f"http://{local_ip}:{local_port}/{podcast_dir}/{full_filename}"
                    log.info(f"Using a locally-hosted URL for this podcast: {url}")
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
