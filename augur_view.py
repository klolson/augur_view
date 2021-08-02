from flask import Flask, render_template, render_template_string, request, abort
import urllib.request, json, os, math, yaml
from pathlib import Path
import urllib3
from concurrent.futures import ThreadPoolExecutor

app = Flask(__name__)

# URL for all endpoint calls, probably won't be hardcoded for much longer
# URL = "http://zephyr.osshealth.io:5222/api/unstable"
# cacheDir = "cache/"

configFile = "config.yml"

settings = { 'approot': "/augur/", 'caching': "cache/", 'serving': "default.osshealth.io", 'paginationOffset': 25 }

def loadSettings():
    try:
        with open(configFile) as file:
            global settings
            settings = yaml.load(file, Loader=yaml.FullLoader)
    except Exception as err:
        print("Error reading application settings from [" + configFile + "], default settings kept:")
        print(err)

def getSetting(key):
    if key == 'approot':
        if settings[key] == "private":
            with open(".app_root") as f:
                settings[key] = f.readline()
    return settings[key]

def loadReports():
    global reports
    try:
        with open(getSetting("reports")) as file:
            reports = yaml.load(file, Loader=yaml.FullLoader)
    except Exception as err:
        print("Error reading reports endpoints from [" + getSetting("reports") + "]:")
        print(err)

loadSettings()

loadReports()

"""
try:
    rootPath = Path(".app_root")
    if rootPath.is_file():
        with open(".app_root") as f:
            approot = f.readline()
    else:
        approot = "/"
except Exception as err:
    print("Error reading application root from .app_root:")
    print(err)
    print("Application root set to [/]")
    approot = "/"
"""

requested = []

def cacheFileExists(filename):
    cache_file = Path(filename)
    if cache_file.is_file() or filename in requested:
        return True
    else:
        return False

def stripStatic(url):
    return url.replace("static/", "")

def toCacheFilename(endpoint):
    return getSetting('caching') + endpoint.replace("/", ".").replace("?", "_").replace("=", "_") + '.agcache'

def toCacheURL(endpoint):
    return stripStatic(getSetting('caching')) + endpoint.replace("/", ".").replace("?", "_").replace("=", "_") + '.agcache'

"""
requestJson:
    Attempts to load JSON data from cache for the given endpoint.
    If no cache file is found, a request is made to the URL for
    the given endpoint and, if successful, the resulting JSON is
    cached for future use. Cached files will be stored with all
    '/' characters replaced with '.' for filesystem compatibility.

@PARAM:     endpoint: String
        A String representation of the requested
        json endpoint (relative to the api root).

@RETURN:    data: JSON
        An object representing the JSON data read
        from either the cache file or the enpoint
        URL. Will return None if an error is
        encountered.
"""
def requestJson(endpoint):
    filename = toCacheFilename(endpoint)
    requestURL = getSetting('serving') + "/" + endpoint
    try:
        if cacheFileExists(filename) and not filename in requested:
            with open(filename) as f:
                data = json.load(f)
        else:
            with urllib.request.urlopen(requestURL) as url:
                data = json.loads(url.read().decode())
                with open(filename, 'w') as f:
                    json.dump(data, f)
        if filename in requested:
            requested.remove(filename)
        return data
    except Exception as err:
        print(err)

def requestPNG(endpoint):
    filename = toCacheFilename(endpoint)
    requestURL = getSetting('serving') + "/" + endpoint
    # print(requestURL)
    try:
        if cacheFileExists(filename) and not filename in requested:
            return toCacheURL(endpoint)
        else:
            urllib.request.urlretrieve(requestURL, filename)
        if filename in requested:
            requested.remove(filename)
        return toCacheURL(endpoint)
    except Exception as err:
        print(err)

def download(url, cmanager, filename):
    if cacheFileExists(filename) and not filename in requested:
        reportImages.append(stripStatic(filename))
        return
    response = cmanager.request('GET', url)
    if "json" in response.headers['Content-Type']:
        print("WARN: unexpected json response in image request for repo")
        print(response.data.decode('utf-8'))
        return
    if response and response.status == 200:
        reportImages.append(stripStatic(filename))
        with open(filename, 'wb') as f:
            f.write(response.data)

def requestReports(repo_id):
    threadPools = []
    global reportImages
    reportImages = []
    for report in reports:
        size = len(reports[report])
        connection_mgr = urllib3.PoolManager(maxsize=size)
        thread_pool = ThreadPoolExecutor(size)
        threadPools.append(thread_pool)
        for url in reports[report]:
            filename = toCacheFilename(url + "?repo_id=" + str(repo_id))
            url = getSetting('serving') + "/" + url + "?repo_id=" + str(repo_id)
            thread_pool.submit(download, url, connection_mgr, filename)

    # Wait for all connections to resolve, then clean up
    for thread_pool in threadPools:
        thread_pool.shutdown()

"""
renderRepos:
    This function renders a list of repos using a given view, while passing query
    data along. This function also processes pagination automatically for the
    range of data provided. If a query is provided and filtering is enabled, the
    data will be filtered using the 'repo_name', 'repo_group_id' or 'rg_name'.
@PARAM:     view: String
        A string representing the template to use for displaying the repos.
@PARAM:     query: String
        The query argument from the previous page.
"""
def renderRepos(view, query, data, page = None, filter = False, pageSource = "repos/views/table"):
    PaginationOffset = getSetting('paginationOffset')
    if(data is None):
        return render_template('index.html', body="repos-" + view, title="Repos")

    if((query is not None) and filter):
        results = []
        for repo in data:
            if (query in repo["repo_name"]) or (query == str(repo["repo_group_id"])) or (query in repo["rg_name"]):
                results.append(repo)
        data = results

    pages = math.ceil(len(data) / PaginationOffset)

    if page is not None:
        page = int(page)
    else:
        page = 1

    x = PaginationOffset * (page - 1)
    data = data[x: x + PaginationOffset]

    print("Pages", pages, "Page", page, "Data", len(data))

    return render_template('index.html', body="repos-" + view, title="Repos", repos=data, query_key=query, activePage=page, pages=pages, offset=PaginationOffset, PS=pageSource, api_url=getSetting('serving'), root=getSetting('approot'))

def renderLoading(dest, query, request):
    requested.append(request)
    return render_template('index.html', body="loading", title="Loading", d=dest, query_key=query, api_url=getSetting('serving'), root=getSetting('approot'))



# ROUTES -----------------------------------------------------------------------

@app.route('/')
@app.route('/repos/views/table')
def repo_table_view():
    query = request.args.get('q')
    page = request.args.get('p')

    #if not cacheFileExists("repos.json"):
    #    return renderLoading("repos/views/table", query, "repos.json")

    data = requestJson("repos")

    return renderRepos("table", query, data, page, True)

@app.route('/repos/views/card')
def repo_card_view():
    query = request.args.get('q')
    return renderRepos("card", query, requestJson("repos"), True)

@app.route('/groups')
def repo_groups_view():
    query = request.args.get('q')
    page = request.args.get('p')

    if(query is not None):
        buffer = []
        data = requestJson("repos")
        for repo in data:
            if query == str(repo["repo_group_id"]) or query in repo["rg_name"]:
                buffer.append(repo)
        return renderRepos("table", query, buffer, page, False, "groups")
    else:
        groups = requestJson("repo-groups")
        return render_template('index.html', body="groups-table", title="Groups", groups=groups, query_key=query, api_url=getSetting('serving'))

#TODO add app.route support for an insights view in the frontend
#app.route('/insights')
#I think a new view should be created for repo insights

@app.route('/repos/views/repo/<id>')
def repo_repo_view(id):
    requestReports(id)
    reportImages.sort()
    # file=requestPNG("contributor_reports/new_contributors_stacked_bar/?repo_id=" + str(id))
    return render_template('index.html', body="repo-info", images=reportImages, title="Repo", repo=id, api_url=getSetting('serving'), root=getSetting('approot'))

# Code 404 response page, for pages not found
@app.errorhandler(404)
def page_not_found(error):
    return render_template('index.html', title='404', api_url=getSetting('serving'), root=getSetting('approot')), 404

# API endpoint to clear server cache
# TODO: Add verification
@app.route('/cache/clear')
def clear_cache():
    try:
        for f in os.listdir(getSetting('caching')):
            os.remove(os.path.join(getSetting('caching'), f))
        return render_template_string('<meta http-equiv="refresh" content="5; URL=' + getSetting('approot') + '"/><p>Cache successfully cleared</p>')
    except Exception as err:
        print(err)
        return render_template_string('<meta http-equiv="refresh" content="5; URL=' + getSetting('approot') + '"/><p>An error occurred while attempting to clear cache</p>')

# API endpoint to reload settings from disk
@app.route('/settings/reload')
def reload_settings():
    loadSettings()
    return render_template_string('<meta http-equiv="refresh" content="5; URL=' + getSetting('approot') + '"/><p>Settings reloaded</p>')
