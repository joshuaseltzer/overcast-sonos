"""
An overcast "API".

Overcast doesn't really offer an official API, so this just sorta apes it.
"""

import requests
import lxml.html
import urllib.parse
import utilities
import logging
from collections import OrderedDict

log = logging.getLogger('overcast-sonos')

UNPLAYED_EPISODE_PREFIX = '* '
EPISODE_CACHE_SIZE = 5
HOSTNAME = "https://overcast.fm"
KNOWN_DURATIONS = {"Scorchin’ Radio": 3600}

class Overcast(object):
    def __init__(self, email, password):
        self.episode_cache = OrderedDict()
        self.session = requests.session()
        r = self.session.post(f"{HOSTNAME}/login", {'email': email, 'password': password})
        doc = lxml.html.fromstring(r.content)
        alert = doc.cssselect('div.alert')
        if alert:
            raise Exception(f"Can't login: {alert[0].text_content().strip()}")

    def _get_html(self, url):
        return lxml.html.fromstring(self.session.get(url).content)

    def get_episode_detail(self, episode_id, updated_offset_millis=-1):
        episode = None

        # check first to see if the episode is in the cache
        if episode_id in self.episode_cache:
            log.debug(f"Retrieving episode details for {episode_id} from the cache")
            episode = self.episode_cache.pop(episode_id)

            # if the offset was not specified or the episode duration was not correctly determined previously, invalidate this cached episode
            if updated_offset_millis == -1 or episode.get('duration', -1) == -1:
                episode = None
            elif updated_offset_millis > -1:
                # update the time remaining
                episode['offsetMillis'] = updated_offset_millis
        
        if not episode:
            log.debug(f"Retrieving episode details for {episode_id} from Overcast")
            episode_href = urllib.parse.urljoin(HOSTNAME, episode_id)
            doc = self._get_html(episode_href)
            audioplayer = doc.cssselect('audio#audioplayer')

            if len(audioplayer) > 0:
                title = doc.cssselect('div.centertext h2')[0].text_content()
                time_elapsed_seconds = int(audioplayer[0].attrib['data-start-time'])
                time_remaining_seconds = self.get_episode_time_remaining_seconds(episode_id, doc)
                if time_remaining_seconds and time_remaining_seconds != -1:
                    duration = time_elapsed_seconds + time_remaining_seconds
                    if time_elapsed_seconds == duration:
                        duration = -1
                else:
                    # if the time remaining could not be determined, override the duration for known podcasts here
                    duration = -1
                    for known_title in KNOWN_DURATIONS.keys():
                        if known_title in title:
                            log.debug(f"Overriding the duration for '{title}")
                            duration = KNOWN_DURATIONS.get(known_title)
                            break

                audioplayer_source = doc.cssselect('audio#audioplayer source')
                episode = {
                    'id': episode_href.lstrip('/'),
                    'title': title,
                    'podcast_title': doc.cssselect('div.centertext h3 a')[0].text_content(),
                    'offsetMillis': time_elapsed_seconds * 1000,
                    'duration': duration,
                    'data_item_id': audioplayer[0].attrib['data-item-id'],
                    'data_sync_version': audioplayer[0].attrib['data-sync-version'],
                    'albumArtURI': doc.cssselect('div.fullart_container img')[0].attrib['src'],
                    'parsed_audio_uri': audioplayer_source[0].attrib['src'],
                    'audio_type': audioplayer_source[0].attrib['type'],
                    'delete_episode_uri': doc.cssselect('a#delete_episode_button')[0].attrib['href'],
                    'data-saved-for-user': int(audioplayer[0].attrib['data-saved-for-user'])
                }
        
        # add the episode to the cache
        if episode:
            self.episode_cache[episode_id] = episode

            # check to see if any episode(s) should be purged from the cache
            while len(self.episode_cache) > EPISODE_CACHE_SIZE:
                log.debug("Removing an episode from the cache")
                self.episode_cache.popitem(last=False)

        return episode

    def get_episode_time_remaining_seconds(self, episode_id, episode_html):
        log.debug(f"Getting the remaining time for episode id {episode_id}")
        podcast_id = episode_html.cssselect('div.centertext h3 a')[0].attrib['href']
        podcast_href = urllib.parse.urljoin(HOSTNAME, podcast_id)
        doc = self._get_html(podcast_href)

        for cell in doc.cssselect('a.extendedepisodecell'):
            if episode_id in cell.attrib['href']:
                return self.get_episode_time_remaining_seconds_from_episode_cell(cell, True)
        return None

    def get_episode_time_remaining_seconds_from_episode_cell(self, cell, is_extended_cell):
        unparsed_time_remaining_index = 1 if is_extended_cell else 2
        unparsed_time_remaining = cell.cssselect('div.singleline')[unparsed_time_remaining_index].text_content()
        time_remaining_seconds = utilities.duration_in_seconds(unparsed_time_remaining)
        return time_remaining_seconds

    def get_all_podcasts(self, unplayed_only=False):
        podcasts = []

        doc = self._get_html(urllib.parse.urljoin(HOSTNAME, podcasts))
        for cell in doc.cssselect('a.feedcell'):
            if 'href' in cell.attrib:
                # perform a check to see if this podcast is unplayed
                unplayed = len(cell.cssselect('svg.unplayed_indicator')) > 0
                if not unplayed_only or (unplayed_only and unplayed):
                    podcasts.append(self.create_podcast_from_cell(cell))

        # sort the result by name
        podcasts.sort(key=lambda item: item.get("title"))

        return podcasts

    def create_podcast_from_cell(self, cell):
        return {
            'id': cell.attrib['href'].lstrip('/'),
            'title': cell.cssselect('div.title')[0].text_content(),
            'albumArtURI': cell.cssselect('img')[0].attrib['src'],
        }

    def get_all_podcast_episodes(self, podcast_id, unplayed_only=False):
        podcast_href = urllib.parse.urljoin(HOSTNAME, podcast_id)
        doc = self._get_html(podcast_href)
        album_art_uri = doc.cssselect('img.art')[0].attrib['src']
        podcast_title = doc.cssselect('h2.centertext')[0].text_content()

        episodes = []
        for cell in doc.cssselect('a.extendedepisodecell'):
            if 'href' in cell.attrib:
                # check to see if this episode is unplayed
                episode_prefix = ''
                if 'usernewepisode' in cell.attrib.get('class', '').split(' '):
                    episode_prefix = UNPLAYED_EPISODE_PREFIX

                # only continue if we are returning all episodes or unplayed episodes
                if not unplayed_only or (unplayed_only and episode_prefix != ''):
                    episode_id = urllib.parse.urljoin(HOSTNAME, cell.attrib.get('href', '')).lstrip('/')
                    episode_title = cell.cssselect('div.titlestack div.title')[0].text_content().strip().replace('\n', '')
                    summary = cell.cssselect('div.titlestack div.caption2')[0].text_content().strip().replace('\n', '')
                    release_date = utilities.convert_release_date(summary)
                    episode = {
                        'id': episode_id,
                        'title': f"{episode_prefix}{episode_title}",
                        'audio_type': 'audio/mpeg', # the actual audio_type is unknown at this point
                        'podcast_title': podcast_title,
                        'albumArtURI': album_art_uri,
                        'summary': summary,
                        'releasedate': release_date
                    }

                    # if we're only returning the unplayed episodes, ensure those are displayed first
                    if unplayed_only:
                        episodes.insert(0, episode)
                    else:
                        episodes.append(episode)
        return episodes

    def update_episode_offset(self, episode, updated_offset_seconds):
        log.debug(f"updated_offset_seconds = {updated_offset_seconds}")
        if episode.get('data-saved-for-user', 0) == 1:
            # Remove episode if less than 60 seconds remaining due to Overcast not giving us accurate episode lengths we have to do this
            # or we end up with finished episodes still showing in the list
            if updated_offset_seconds >= (episode.get('duration', -1) - 60):
                self.delete_episode(episode)
            else:
                url = urllib.parse.urljoin(HOSTNAME, f"/podcasts/set_progress/{episode.get('data_item_id', '')}")
                params = {
                    'p': updated_offset_seconds,
                    'speed': 0,
                    'v': episode.get('data_sync_version')
                }
                log.debug(f"Updating offset of episode with id '{episode.get('id', '')}' to {updated_offset_seconds}")
                self.session.post(url, params)

    def delete_episode(self, episode):
        delete_episode_uri = episode.get('delete_episode_uri')
        if delete_episode_uri:
            url = urllib.parse.urljoin(HOSTNAME, delete_episode_uri)
            log.debug(f"Deleting episode with id '{episode.get('id', '')}'")
            self.session.post(url)
