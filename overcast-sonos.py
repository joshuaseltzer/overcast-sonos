import os
import logging
import uuid
import time
from threading import Thread
from pathlib import Path
from overcast import Overcast, utilities
from pysimplesoap.server import SoapDispatcher, SOAPHandler
from http.server import HTTPServer, SimpleHTTPRequestHandler


logging.basicConfig(level=logging.INFO)
log = logging.getLogger('overcast-sonos')

DEFAULT_ALBUM_ART_URI = 'http://is3.mzstatic.com/image/thumb/Purple111/v4/20/5b/5e/205b5ef7-ee0e-7d0c-2d11-12f611c579f4/source/175x175bb.jpg'
ALL_PODCASTS_ID = 'all_podcasts'
UNPLAYED_PODCASTS_ID = 'unplayed_podcasts'
UNPLAYED_PODCAST_ID_PREFIX = 'podcast_unplayed'
PODCAST_ID_PREFIX = 'podcast'
REPORT_PLAY_SECONDS_INTERVAL = 30

# grab some variables from the environment variables
OVERCAST_USERNAME = os.environ.get('OVERCAST_USERNAME')
OVERCAST_PASSWORD = os.environ.get('OVERCAST_PASSWORD')
OVERCAST_SONOS_PORT = int(os.environ.get('OVERCAST_SONOS_PORT', 8140))
OVERCAST_LOCAL_HOST_IP = os.environ.get('OVERCAST_LOCAL_HOST_IP')
OVERCAST_LOCAL_PORT = int(os.environ.get('OVERCAST_LOCAL_PORT', 8080))
OVERCAST_LOCAL_DOWNLOAD_DIR = os.environ.get('OVERCAST_LOCAL_DOWNLOAD_DIR', 'podcasts')
OVERCAST_LOCAL_KEEP_FOR_DAYS = int(os.environ.get('OVERCAST_LOCAL_KEEP_FOR_DAYS', 30))


class CustomSOAPHandler(SOAPHandler):
    def do_GET(self):
        log.debug('PATH ==> %s', self.path)
        if self.path == '/presentation_map':
            self.send_response(http.HTTPStatus.OK)
            self.send_header('Content-type', 'text/xml')
            self.end_headers()
            self.wfile.write('''<?xml version="1.0" encoding="UTF-8"?>
            <Presentation>
                <PresentationMap type="DisplayType">
                    <RootNodeDisplayType>
                        <DisplayMode>LIST</DisplayMode>
                    </RootNodeDisplayType>
                </PresentationMap>
                <PresentationMap type="QuickSkips">
                    <QuickSkip type="episode.podcast" forwardSeconds="45" backwardSeconds="10"/>
                </PresentationMap>
            </Presentation>
            '''.encode("utf-8"))
            log.info('PresentationMap has been sent.')
            return
        else:
            return SOAPHandler.do_GET(self)


class CustomHTTPRequestHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=OVERCAST_LOCAL_DOWNLOAD_DIR, **kwargs)


# the dispatcher which is responsible for sending the Soap payloads
dispatcher = SoapDispatcher(
    'overcast-sonos',
    location=f'http://localhost:{OVERCAST_SONOS_PORT}/',
    namespace='http://www.sonos.com/Services/1.1',
    trace=True,
    debug=True
)


# create the instance responsible for interacting with the Overcast website
overcast = Overcast(OVERCAST_USERNAME, OVERCAST_PASSWORD)


# define the Soap objects that the service is expecting
mediaCollection = {
    'id': str,
    'title': str,
    'itemType': str,
    'artistId': str,
    'artist': str,
    'albumArtURI': str,
    'canPlay': bool,
    'canEnumerate': bool,
    'canAddToFavorites': bool,
    'canScroll': bool,
    'canSkip': bool
}
positionInformation = {
    'id': str,
    'index': int, # always 0, "reserved for future use" by Sonos
    'offsetMillis': int
}
trackMetadata = {
    'artist': str,
    'albumArtist': str,
    'albumArtURI': str,
    'genreId': str,
    'duration': int,
    'canResume': bool
}
mediaMetadata = {
    'id': str,
    'title': str,
    'mimeType': str,
    'itemType': str,
    'trackMetadata': trackMetadata
}


# starts a local server instance to host podcast files directly
def start_local_server():
    log.info(f'Creating local server (accessible from {OVERCAST_LOCAL_HOST_IP}) to host podcast files from {OVERCAST_LOCAL_DOWNLOAD_DIR} on port {OVERCAST_LOCAL_PORT}.')
    server = HTTPServer(("", OVERCAST_LOCAL_PORT), CustomHTTPRequestHandler)
    server.serve_forever()


# cleans up the given directory by removing any files older than the specified days
def cleanup_directory(directory, keep_for_days):
    log.info(f'Cleaning directory "{directory}" by deleting files over {keep_for_days} days old.')

    cutoff_time = time.time() - (keep_for_days * 86400)
    for file in list(Path(directory).rglob("*")):
        if not file.is_file():
            continue

        if file.stat().st_ctime < cutoff_time:
            log.info(f'Removing old file "{file}."')
            try:
                file.unlink()
            except Exception
                log.error(f'Could not delete "{file}."')


# returns a media collection object for a podcast entry
def create_podcast_media_collection(podcast, unplayed_only=False):
    # the collection id will differ if it's an unplayed podcast
    if unplayed_only:
        id_prefix = UNPLAYED_PODCAST_ID_PREFIX
    else:
        id_prefix = PODCAST_ID_PREFIX

    return {
        'id': f"{id_prefix}/{podcast['id']}",
        'title': podcast['title'],
        'albumArtURI': podcast['albumArtURI'],
        'itemType': 'album',
        'semanticType': 'podcast',
        'canPlay': False,
        'producer': '',
    }

###

def getSessionId(username, password):
    log.debug('at=getSessionId username=%s password=%s', username, password)
    return username


dispatcher.register_function(
    'getSessionId',
    getSessionId,
    returns={
        'getSessionIdResult': str
    },
    args={
        'username': str,
        'password': str
    }
)

###

# Gets metadata for podcasts and episodes, depending on which id is sent from Sonos
def getMetadata(id, index, count, recursive=False):
    log.debug('at=getMetadata id=%s index=%s count=%s recursive=%s', id, index, count, recursive)

    if id == 'root':
        # the root view will show a 'all podcasts' subcollection, 'unplayed podcasts' subcollection, and any unplayed podcasts individually
        all_unplayed_podcasts = overcast.get_all_podcasts(unplayed_only=True)
        podcasts = all_unplayed_podcasts[index:index + count]
        response = {
            'getMetadataResult': [
                {
                    'index': index,
                    'count': len(podcasts) + 2,
                    'total': len(all_unplayed_podcasts) + 2
                }
            ]
        }

        # add a collection that will list all podcasts
        response['getMetadataResult'].append(
            {
                'mediaCollection': {
                    'id': ALL_PODCASTS_ID,
                    'title': 'All Podcasts',
                    'itemType': 'collection',
                    'canPlay': False,
                    'albumArtURI': DEFAULT_ALBUM_ART_URI
                }
            }
        )

        # add a collection that will list all unplayed podcasts
        response['getMetadataResult'].append(
            {
                'mediaCollection': {
                    'id': UNPLAYED_PODCASTS_ID,
                    'title': 'Unplayed Podcasts',
                    'itemType': 'collection',
                    'canPlay': False,
                    'albumArtURI': DEFAULT_ALBUM_ART_URI
                }
            }
        )

        # add any unplayed podcasts that might exist
        for podcast in podcasts:
            response['getMetadataResult'].append(
                {
                    'mediaCollection': create_podcast_media_collection(podcast, unplayed_only=True)
                }
            )
    elif id == ALL_PODCASTS_ID or id == UNPLAYED_PODCASTS_ID:
        # this code path will create a collection shows a list of all podcasts or unplayed podcasts
        all_podcasts = overcast.get_all_podcasts(unplayed_only=(id == UNPLAYED_PODCASTS_ID))
        podcasts = all_podcasts[index:index + count]
        response = {
            'getMetadataResult': [
                {
                    'index': index,
                    'count': len(podcasts),
                    'total': len(all_podcasts)
                }
            ]
        }
        for podcast in podcasts:
            response['getMetadataResult'].append(
                {
                    'mediaCollection': create_podcast_media_collection(podcast, unplayed_only=(id == UNPLAYED_PODCASTS_ID))
                }
            )
    elif id.startswith(PODCAST_ID_PREFIX):
        # this code path will show episodes available for a given podcast
        id_prefix, podcast_id = id.split('/', 1)
        all_episodes = overcast.get_all_podcast_episodes(podcast_id, unplayed_only=(id_prefix == UNPLAYED_PODCAST_ID_PREFIX))
        episodes = all_episodes[index:index+count]
        response = {
            'getMetadataResult': [
                {
                    'index': index,
                    'count': len(episodes),
                    'total': len(all_episodes)
                }
            ]
        }
        for episode in episodes:
            response['getMetadataResult'].append(
                {
                    'mediaMetadata': {
                        'id': 'episodes/' + episode['id'],
                        'title': episode['title'],
                        'mimeType': episode['audio_type'],
                        'itemType': 'track',
                        'semanticType': 'episode.podcast',
                        'summary': episode['summary'],
                        'releasedate': episode['releasedate'],
                        'trackMetadata': {
                            'artist': episode['podcast_title'],
                            'albumArtist': episode['podcast_title'],
                            'albumArtURI': episode['albumArtURI'],
                            'genreId': 'podcast',
                            'canResume': True,
                        }
                    }
                }
            )
    else:
        logging.error('unknown getMetadata id id=%s', id)
        response = {
            'getMetadataResult': [
                {
                    'index': 0,
                    'count': 0,
                    'total': 0
                }
            ]
        }

    log.debug('at=getMetadata response=%s', response)
    return response


dispatcher.register_function(
    'getMetadata',
    getMetadata,
    returns={
        'getMetadataResult': {
            'index': int,
            'count': int,
            'total': int,
            'mediaCollection': mediaCollection
        }
    },
    args={
        'id': str,
        'index': int,
        'count': int,
        'recursive': bool
    }
)

###

# Get the metadata for a single item/episode
def getMediaMetadata(id):
    log.debug('at=getMediaMetadata id=%s', id)
    _, episode_id = id.rsplit('/', 1)
    log.debug('at=getMediaMetadata episode_id=%s', episode_id)
    episode = overcast.get_episode_detail(episode_id)
    if episode is not None:
        response = {
            'getMediaMetadataResult': {
                'mediaMetadata': {
                    'id': id,
                    'title': episode['title'],
                    'mimeType': episode['audio_type'],
                    'itemType': 'track',
                    'trackMetadata': {
                        'artist': episode['podcast_title'],
                        'albumArtist': episode['podcast_title'],
                        'albumArtURI': episode['albumArtURI'],
                        'genreId': 'podcast',
                        'duration': episode['duration'],
                        'canResume': True,
                    }
                }
            }
        }
        log.debug('at=getMediaMetadata response=%s', response)
        return response
    else:
        return None


dispatcher.register_function(
    'getMediaMetadata',
    getMediaMetadata,
    returns={
        'getMediaMetadataResult': mediaMetadata
    },
    args={
        'id': str
    }
)

###

# Get the URI for an episode
def getMediaURI(id):
    log.debug('at=getMediaURI id=%s', id)
    _, episode_id = id.rsplit('/', 1)
    episode = overcast.get_episode_detail(episode_id)
    response = {
        'getMediaURIResult': utilities.final_redirect_url(
            episode['parsed_audio_uri'],
            episode['title'],
            episode['podcast_title'],
            OVERCAST_LOCAL_HOST_IP,
            OVERCAST_LOCAL_PORT,
            OVERCAST_LOCAL_DOWNLOAD_DIR
        ),
        'positionInformation': {
            'id': 'episodes/' + episode['id'],
            'index': 0,
            'offsetMillis': episode['offsetMillis']
        }
    }
    log.debug('at=getMediaURI response=%s', response)
    return response


dispatcher.register_function(
    'getMediaURI',
    getMediaURI,
    returns={
        'getMediaURIResult': str,
        'positionInformation': positionInformation
    },
    args={
        'id': str
    }
)

###

def getLastUpdate():
    log.debug('at=getLastUpdate')
    return {
        'getLastUpdateResult': {
            'catalog': str(uuid.uuid4()),
            'favorites': '0',
            'pollInterval': 60
        }
    }


dispatcher.register_function(
    'getLastUpdate',
    getLastUpdate,
    returns={
        'getLastUpdateResult': {
            'autoRefreshEnabled': bool,
            'catalog': str,
            'favorites': str,
            'pollInterval': int
        }
    },
    args={}
)

###

def reportPlaySeconds(id, seconds, offsetMillis, contextId):
    episode_id = id.rsplit('/', 1)[-1]
    log.debug('at=reportPlaySeconds and id=%s, seconds=%d, offsetMillis=%d, contextId=%s, episode_id=%s', id, seconds, offsetMillis, contextId, episode_id)
    episode = overcast.get_episode_detail(episode_id, offsetMillis)
    overcast.update_episode_offset(episode, offsetMillis/1000)
    return {
        'reportPlaySecondsResult': {
            'interval': REPORT_PLAY_SECONDS_INTERVAL
        }
    }


dispatcher.register_function(
    'reportPlaySeconds',
    reportPlaySeconds,
    returns={
        'reportPlaySecondsResult': {
            'interval': int
        }
    },
    args={
        'id': str,
        'seconds': int,
        'offsetMillis': int,
        'contextId': str
    }
)

###

def reportPlayStatus(id, status, offsetMillis, contextId):
    episode_id = id.rsplit('/', 1)[-1]
    log.debug('at=reportPlayStatus and id=%s, status=%s, contextId=%s, offsetMillis=%d, episode_id=%s', id, status, contextId, offsetMillis, episode_id)
    episode = overcast.get_episode_detail(episode_id, offsetMillis)
    overcast.update_episode_offset(episode, offsetMillis/1000)


dispatcher.register_function(
    'reportPlayStatus',
    reportPlayStatus,
    returns={},
    args={
        'id': str,
        'status': str,
        'offsetMillis': int,
        'contextId': str
    }
)

###

def setPlayedSeconds(id, seconds, offsetMillis, contextId):
    episode_id = id.rsplit('/', 1)[-1]
    log.debug('at=setPlayedSeconds and id=%s, seconds=%d, offsetMillis=%d, contextId=%s, episode_id=%s', id, seconds, offsetMillis, contextId, episode_id)
    episode = overcast.get_episode_detail(episode_id, offsetMillis)
    overcast.update_episode_offset(episode, offsetMillis/1000)


dispatcher.register_function(
    'setPlayedSeconds',
    setPlayedSeconds,
    returns={},
    args={
        'id': str,
        'seconds': int,
        'offsetMillis': int,
        'contextId': str
    }
)


if __name__ == '__main__':
    log.debug('at=__main__')

    # potentially create a local server to host podcast files if the host IP address was provided
    if OVERCAST_LOCAL_HOST_IP:
        Thread(target=start_local_server).start()

        # perform a cleanup on the local directory and schedule a daily cleanup
        cleanup_directory(directory=OVERCAST_LOCAL_DOWNLOAD_DIR, keep_for_days=OVERCAST_LOCAL_KEEP_FOR_DAYS)
        schedule.every().day.at('02:00').do(cleanup_directory, directory=OVERCAST_LOCAL_DOWNLOAD_DIR, keep_for_days=OVERCAST_LOCAL_KEEP_FOR_DAYS)

    # start the main Soap server
    httpd = HTTPServer(("", OVERCAST_SONOS_PORT), CustomSOAPHandler)
    httpd.dispatcher = dispatcher
    httpd.serve_forever()
