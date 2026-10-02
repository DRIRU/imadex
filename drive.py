"""Read-only Google Drive source with OAuth PKCE and server-side credentials."""
import base64
import hashlib
import json
import secrets
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

lock = threading.RLock()
pending = {}
SCOPE = 'https://www.googleapis.com/auth/drive.readonly'
FILE_FIELDS = 'id,name,mimeType,size,modifiedTime,md5Checksum,imageMediaMetadata,parents,trashed'


class DriveError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(f'Google Drive request failed ({code}). Check access, reconnect, or retry later.')


def configuration(data):
    path = data / 'google-client.json'
    if not path.exists():
        raise ValueError('Add your Google Desktop OAuth client file at data/google-client.json. See README.md for setup.')
    config = json.loads(path.read_text()).get('installed')
    if not config or not config.get('client_id'):
        raise ValueError('Use a Google OAuth client of type Desktop app.')
    return config


def state(data):
    return {'configured': (data / 'google-client.json').exists(), 'connected': (data / 'google-token.json').exists()}


def authorize(data, port):
    config = configuration(data)
    nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    redirect = f'http://127.0.0.1:{port}/oauth/callback'
    with lock:
        pending.clear()
        pending[nonce] = (verifier, redirect, time.time())
    return 'https://accounts.google.com/o/oauth2/v2/auth?' + urlencode({
        'client_id': config['client_id'], 'redirect_uri': redirect, 'response_type': 'code',
        'scope': SCOPE, 'state': nonce, 'code_challenge_method': 'S256',
        'code_challenge': base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('='),
        'access_type': 'offline', 'prompt': 'consent'})


def token_request(values):
    try:
        with urlopen(Request('https://oauth2.googleapis.com/token', data=urlencode(values).encode()), timeout=30) as response:
            return json.load(response)
    except HTTPError as error:
        raise ValueError(f'Google sign-in failed ({error.code}). Reconnect Google Drive.') from None


def save_token(data, token):
    token['expires_at'] = time.time() + token.get('expires_in', 3600)
    temporary = data / 'google-token.tmp'
    temporary.write_text(json.dumps(token))
    temporary.replace(data / 'google-token.json')


def callback(data, values):
    with lock:
        entry = pending.pop(values.get('state', ''), None)
        if not entry or time.time() - entry[2] > 600:
            raise ValueError('Sign-in expired or invalid. Start again from the dashboard.')
        if values.get('error'):
            raise ValueError('Google sign-in was cancelled. You can try again from the dashboard.')
        config = configuration(data)
        token = token_request({'client_id': config['client_id'], 'client_secret': config.get('client_secret', ''),
            'code': values.get('code', ''), 'code_verifier': entry[0], 'redirect_uri': entry[1], 'grant_type': 'authorization_code'})
        save_token(data, token)


def access_token(data):
    with lock:
        path = data / 'google-token.json'
        if not path.exists():
            raise ValueError('Connect Google Drive first.')
        token = json.loads(path.read_text())
        if token.get('expires_at', 0) < time.time() + 60:
            config = configuration(data)
            refreshed = token_request({'client_id': config['client_id'], 'client_secret': config.get('client_secret', ''),
                'refresh_token': token.get('refresh_token', ''), 'grant_type': 'refresh_token'})
            token.update(refreshed)
            save_token(data, token)
        return token['access_token']


def request(data, route, params=None):
    from performance import span
    url = 'https://www.googleapis.com/drive/v3/' + route
    if params:
        url += '?' + urlencode(params)
    try:
        with span('drive_auth'):
            token = access_token(data)
        return urlopen(Request(url, headers={'Authorization': 'Bearer ' + token}), timeout=60)
    except HTTPError as error:
        raise DriveError(error.code) from None


def children(data, parent):
    page = None
    while True:
        params = {'q': f"'{parent}' in parents and trashed=false and (mimeType contains 'image/' or mimeType='application/vnd.google-apps.folder')",
                  'fields': 'nextPageToken,incompleteSearch,files(' + FILE_FIELDS + ')', 'pageSize': 1000,
                  'supportsAllDrives': 'true', 'includeItemsFromAllDrives': 'true'}
        if page:
            params['pageToken'] = page
        with request(data, 'files', params) as response:
            result = json.load(response)
        if result.get('incompleteSearch'):
            raise ValueError('Drive returned an incomplete listing. Retry without changing the current catalog.')
        yield from result.get('files', [])
        page = result.get('nextPageToken')
        if not page:
            return


def walk_nodes(data, root):
    root_file = metadata(data, root, FILE_FIELDS)
    if root_file.get('trashed') or root_file.get('mimeType') != 'application/vnd.google-apps.folder':
        raise ValueError('Selected Drive root is unavailable or no longer a folder.')
    yield root_file
    queue, visited = [root], set()
    while queue:
        parent = queue.pop()
        if parent in visited:
            continue
        visited.add(parent)
        for file in children(data, parent):
            yield file
            if file['mimeType'] == 'application/vnd.google-apps.folder':
                queue.append(file['id'])


def start_cursor(data):
    with request(data, 'changes/startPageToken', {'supportsAllDrives': 'true'}) as response:
        return json.load(response)['startPageToken']


def changes_page(data, cursor):
    with request(data, 'changes', {'pageToken': cursor, 'pageSize': 1000, 'spaces': 'drive',
        'includeRemoved': 'true', 'supportsAllDrives': 'true', 'includeItemsFromAllDrives': 'true',
        'fields': 'nextPageToken,newStartPageToken,changes(fileId,removed,file(' + FILE_FIELDS + '))'}) as response:
        return json.load(response)


def metadata(data, file_id, fields):
    with request(data, 'files/' + file_id, {'fields': fields, 'supportsAllDrives': 'true'}) as response:
        return json.load(response)


def ancestors(data, file_id):
    """Resolve parent IDs for selected-root overlap checks without listing photos."""
    visited=set();queue=[file_id]
    while queue:
        key=queue.pop()
        if key in visited:continue
        visited.add(key)
        file=metadata(data,key,'id,parents')
        queue.extend(file.get('parents',[]))
    return visited


def walk(data, folder_id):
    queue, visited = [folder_id], set()
    while queue:
        parent = queue.pop()
        if parent in visited:
            continue
        visited.add(parent)
        page = None
        while True:
            params = {'q': f"'{parent}' in parents and trashed=false and (mimeType contains 'image/' or mimeType='application/vnd.google-apps.folder')",
                'fields': 'nextPageToken,incompleteSearch,files(id,name,mimeType,size,modifiedTime,md5Checksum,imageMediaMetadata)',
                'pageSize': 1000, 'supportsAllDrives': 'true', 'includeItemsFromAllDrives': 'true'}
            if page:
                params['pageToken'] = page
            with request(data, 'files', params) as response:
                result = json.load(response)
            if result.get('incompleteSearch'):
                raise ValueError('Drive returned an incomplete listing. Please retry the scan.')
            for file in result.get('files', []):
                if file['mimeType'] == 'application/vnd.google-apps.folder':
                    queue.append(file['id'])
                else:
                    yield file
            page = result.get('nextPageToken')
            if not page:
                break


def thumbnail(data, file_id):
    from urllib.parse import urlparse
    link = metadata(data, file_id, 'thumbnailLink').get('thumbnailLink')
    if not link:
        raise ValueError('Google Drive has no thumbnail for this image.')
    url = urlparse(link)
    if url.scheme != 'https' or not (url.hostname.endswith('.googleusercontent.com') or url.hostname.endswith('.google.com')):
        raise ValueError('Unexpected Google thumbnail address.')
    return urlopen(Request(link, headers={'Authorization': 'Bearer ' + access_token(data)}), timeout=30)
