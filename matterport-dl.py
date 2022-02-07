#!/usr/bin/env python3

'''
Downloads virtual tours from matterport.
Usage is either running this program with the URL/pageid as an argument or calling the initiateDownload(URL/pageid) method.
'''
import concurrent.futures
import decimal
import json
import logging
import os
import pathlib
import re
import shutil
import sys
import time
import urllib.request
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.error import HTTPError
from urllib.parse import urlparse

import requests
from tqdm import tqdm

# Weird hack
import files

access_urls = []
SHOWCASE_INTERNAL_NAME = "showcase-internal.js"


def make_dirs(dirname):
    pathlib.Path(dirname).mkdir(parents=True, exist_ok=True)


def get_variants():
    variants = []
    depths = ["512", "1k", "2k", "4k"]
    for depth in range(4):
        z = depths[depth]
        for x in range(2 ** depth):
            for y in range(2 ** depth):
                for face in range(6):
                    variants.append(f"{z}_face{face}_{x}_{y}.jpg")
    return variants


def download_uuid(access_url, uuid):
    download_file(access_url.format(filename=f'{uuid}_50k.dam'), f'{uuid}_50k.dam')
    shutil.copy(f'{uuid}_50k.dam', f'..{os.path.sep}{uuid}_50k.dam')
    cur_file = ""
    try:
        for i in range(1000):
            cur_file = access_url.format(filename=f'{uuid}_50k_texture_jpg_high/{uuid}_50k_{i:03d}.jpg')
            download_file(cur_file, f'{uuid}_50k_texture_jpg_high/{uuid}_50k_{i:03d}.jpg')
            cur_file = access_url.format(filename=f'{uuid}_50k_texture_jpg_low/{uuid}_50k_{i:03d}.jpg')
            download_file(cur_file, f'{uuid}_50k_texture_jpg_low/{uuid}_50k_{i:03d}.jpg')
    except Exception as ex:
        logging.warning(f'Exception downloading file: {cur_file} of: {str(ex)}')
        pass  # very lazy and bad way to only download required files


def download_sweeps(access_url, sweeps):
    with tqdm(total=(len(sweeps) * len(get_variants()))) as pbar:
        with concurrent.futures.ThreadPoolExecutor(max_workers=32) as executor:
            for sweep in sweeps:
                for variant in get_variants():
                    pbar.update(1)
                    executor.submit(download_file, access_url.format(filename=f'tiles/{sweep}/{variant}') + "&imageopt=1",
                                    f'tiles/{sweep}/{variant}')
                    while executor._work_queue.qsize() > 64:
                        time.sleep(0.01)


def download_file_with_json_post(url, file, post_json_str, descriptor):
    global PROXY
    if "/" in file:
        make_dirs(os.path.dirname(file))
    if os.path.exists(file):
        # skip already downloaded files except index.html which is really json possibly with newer access keys?
        logging.debug(f'Skipping json post to url: {url} ({descriptor}) as already downloaded')

    opener = get_url_opener(PROXY)
    opener.addheaders.append(('Content-Type', 'application/json'))

    req = urllib.request.Request(url)

    for header in opener.addheaders:  # not sure why we can't use the opener itself but it doesn't override it properly
        req.add_header(header[0], header[1])

    body_bytes = bytes(post_json_str, "utf-8")
    req.add_header('Content-Length', len(body_bytes))
    resp = urllib.request.urlopen(req, body_bytes)
    with open(file, 'w', encoding="UTF-8") as the_file:
        the_file.write(resp.read().decode("UTF-8"))
    logging.debug(f'Successfully downloaded w/ JSON post to: {url} ({descriptor}) to: {file}')


def download_file(url, file, post_data=None):
    global access_urls
    url = get_or_replace_key(url, False)

    if "/" in file:
        make_dirs(os.path.dirname(file))
    if "?" in file:
        file = file.split('?')[0]

    if os.path.exists(file):
        # skip already downloaded files except idnex.html which is really json possibly wit hnewer access keys?
        logging.debug(f'Skipping url: {url} as already downloaded')
        return
    try:
        _filename, headers = urllib.request.urlretrieve(url, file, None, post_data)
        logging.debug(f'Successfully downloaded: {url} to: {file}')
        return
    except HTTPError as err:
        logging.warning(f'URL error downloading {url} of will try alt: {str(err)}')

        # Try again but with different access_urls (very hacky!)
        if "?t=" in url:
            for access_url in access_urls:
                url2 = ""
                try:
                    url2 = f"{url.split('?')[0]}?{access_url}"
                    urllib.request.urlretrieve(url2, file)
                    logging.debug(f'Successfully downloaded through alt: {url2} to: {file}')
                    return
                except HTTPError as err:
                    logging.warning(f'URL error alt method tried url {url2} downloading of: {str(err)}')
                    pass
        logging.error(f'Failed to succeed for url {url}')
        raise Exception


def download_graph_models(pageid):
    global GRAPH_DATA_REQ
    make_dirs("api/mp/models")

    for key in GRAPH_DATA_REQ:
        file_path = f"api/mp/models/graph_{key}.json"
        download_file_with_json_post(
            "https://my.matterport.com/api/mp/models/graph",
            file_path,
            GRAPH_DATA_REQ[key],
            key
        )


def download_assets(base):

    download_file(base + "js/showcase.js", "js/showcase.js")
    with open(f"js/showcase.js", "r", encoding="UTF-8") as f:
        showcase_cont = f.read()
    # lets try to extract the js files it might be loading and make sure we know them
    js_extracted = re.findall(r'\.e\(([0-9]{2,3})\)', showcase_cont)
    js_extracted.sort()
    for js in js_extracted:
        if js not in files.js_files:
            print(f'JS FILE EXTRACTED BUT not known, please file a github issue and tell us to add: {js}.js, will '
                  f'download for you though:)')
            files.js_files.append(js)

    for image in files.image_files:
        if not image.endswith(".jpg") and not image.endswith(".svg"):
            image = image + ".png"
        files.assets.append("images/" + image)
    for js in files.js_files:
        iles.assets.append("js/" + js + ".js")
    for f in files.font_files:
        iles.assets.extend(["fonts/" + f + ".woff", "fonts/" + f + ".woff2"])
    for lc in files.language_codes:
        iles.assets.append("locale/messages/strings_" + lc + ".json")
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
        for asset in files.assets:
            local_file = asset
            if local_file.endswith('/'):
                local_file = local_file + "index.html"
            executor.submit(download_file, f"{base}{asset}", local_file)


def set_access_urls(page_id):
    global access_urls
    with open(f"api/player/models/{page_id}/files_type2", "r", encoding="UTF-8") as f:
        file_json = json.load(f)
        access_urls.append(file_json["base.url"].split("?")[-1])
    with open(f"api/player/models/{page_id}/files_type3", "r", encoding="UTF-8") as f:
        file_json = json.load(f)
        access_urls.append(file_json["templates"][0].split("?")[-1])


def download_info(page_id):
    assets = [f"api/v1/jsonstore/model/highlights/{page_id}", f"api/v1/jsonstore/model/Labels/{page_id}",
              f"api/v1/jsonstore/model/mattertags/{page_id}", f"api/v1/jsonstore/model/measurements/{page_id}",
              f"api/v1/player/models/{page_id}/thumb?width=1707&dpr=1.5&disable=upscale",
              f"api/v1/player/models/{page_id}/", f"api/v2/models/{page_id}/sweeps", "api/v2/users/current",
              f"api/player/models/{page_id}/files"]
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
        for asset in assets:
            local_file = asset
            if local_file.endswith('/'):
                local_file = local_file + "index.html"
            executor.submit(download_file, f"https://my.matterport.com/{asset}", local_file)
    make_dirs("api/mp/models")
    with open(f"api/mp/models/graph", "w", encoding="UTF-8") as f:
        f.write('{"data": "empty"}')
    for i in range(1, 4):
        download_file(f"https://my.matterport.com/api/player/models/{page_id}/files?type={i}",
                     f"api/player/models/{page_id}/files_type{i}")
    set_access_urls(page_id)


def download_pics(page_id):
    with open(f"api/v1/player/models/{page_id}/index.html", "r", encoding="UTF-8") as f:
        model_data = json.load(f)
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
        for image in model_data["images"]:
            executor.submit(download_file, image["src"], urlparse(image["src"]).path[1:])


def download_model(page_id, access_url):
    global ADVANCED_DOWNLOAD_ALL
    with open(f"api/v1/player/models/{page_id}/index.html", "r", encoding="UTF-8") as f:
        model_data = json.load(f)
    access_id = re.search(r'models/([a-z0-9-_./~]*)/\{filename\}', access_url).group(1)
    make_dirs(f"models/{access_id}")
    os.chdir(f"models/{access_id}")
    download_uuid(access_url, model_data["job"]["uuid"])
    download_sweeps(access_url, model_data["sweeps"])


# Patch showcase.js to fix expiration issue
def patchShowcase():
    global SHOWCASE_INTERNAL_NAME
    with open("js/showcase.js", "r", encoding="UTF-8") as f:
        j = f.read()
    j = re.sub(r"&&\(!e.expires\|\|.{1,10}\*e.expires>Date.now\(\)\)", "", j)
    j = j.replace(f'"/api/mp/', '`${window.location.pathname}`+"api/mp/')
    j = j.replace("${this.baseUrl}", "${window.location.origin}${window.location.pathname}")
    j = j.replace('e.get("https://static.matterport.com/geoip/",{responseType:"json",priority:n.RequestPriority.LOW})',
                  '{"country_code":"US","country_name":"united states","region":"CA","city":"los angeles"}')
    with open(f"js/{SHOWCASE_INTERNAL_NAME}", "w", encoding="UTF-8") as f:
        f.write(j)
    j = j.replace(f'"POST"', '"GET"')  # no post requests for external hosted
    with open("js/showcase.js", "w", encoding="UTF-8") as f:
        f.write(j)


def d_range(x, y, jump):
    while x < y:
        yield float(x)
        x += decimal.Decimal(jump)


KNOWN_ACCESS_KEY = None


def get_or_replace_key(url, is_read_key):
    global KNOWN_ACCESS_KEY
    key_regex = r'(t=2\-.+?\-0)'
    match = re.search(key_regex, url)
    if match is None:
        return url
    url_key = match.group(1)
    if KNOWN_ACCESS_KEY is None and is_read_key:
        KNOWN_ACCESS_KEY = url_key
    elif not is_read_key and KNOWN_ACCESS_KEY:
        url = url.replace(url_key, KNOWN_ACCESS_KEY)
    return url


def download_page(pageid):
    global ADVANCED_DOWNLOAD_ALL
    make_dirs(pageid)
    os.chdir(pageid)

    ADV_CROP_FETCH = [
        {
            "start": "width=512&crop=1024,1024,",
            "increment": '0.5'
        },
        {
            "start": "crop=512,512,",
            "increment": '0.25'
        }
    ]

    try:
        logging.basicConfig(filename='run_report.log', encoding='utf-8', level=logging.DEBUG,
                            format='%(asctime)s %(levelname)-8s %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    except ValueError:
        logging.basicConfig(filename='run_report.log', level=logging.DEBUG,
                            format='%(asctime)s %(levelname)-8s %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    logging.debug(f'Started up a download run')
    page_root_dir = os.path.abspath('.')
    print("Downloading base page...")
    r = requests.get(f"https://my.matterport.com/show/?m={pageid}")
    r.encoding = "utf-8"
    staticbase = re.search(r'<base href="(https://static.matterport.com/.*?)">', r.text).group(1)
    match = re.search(r'"(https://cdn-\d*\.matterport\.com/models/[a-z0-9\-_/.]*/)([{}0-9a-z_/<>.]+)(\?t=.*?)"', r.text)
    if match:
        accessurl = f'{match.group(1)}~/{{filename}}{match.group(3)}'
        print(accessurl)
    else:
        raise Exception("Can't find urls")

    file_type_content = requests.get(
        f"https://my.matterport.com/api/player/models/{pageid}/files?type=3")  # get a valid access key, there are a few but this is a common client used one, this also makes sure it is fresh
    get_or_replace_key(file_type_content.text, True)
    if ADVANCED_DOWNLOAD_ALL:
        print("Doing advanced download of dollhouse/floorplan data...")
        ## Started to parse the modeldata further.  As it is error prone tried to try catch silently for failures. There is more data here we could use for example:
        ## queries.GetModelPrefetch.data.model.locations[X].pano.skyboxes[Y].tileUrlTemplate
        ## queries.GetModelPrefetch.data.model.locations[X].pano.skyboxes[Y].urlTemplate
        ## queries.GetModelPrefetch.data.model.locations[X].pano.resolutions[Y] <--- has the resolutions they offer for this one
        ## goal here is to move away from some of the access url hacks, but if we are successful on try one won't matter:)

        try:
            match = re.search(r'window.MP_PREFETCHED_MODELDATA = (\{.+?\}\}\});', r.text)
            if match:
                preload_json = json.loads(match.group(1))
                # download dam files
                base_node = preload_json["queries"]["GetModelPrefetch"]["data"]["model"]["assets"]
                for mesh in base_node["meshes"]:
                    try:
                        download_file(mesh["url"], urlparse(mesh["url"]).path[
                                                  1:])  # not expecting the non 50k one to work but mgiht as well try
                    except:
                        pass
                for texture in base_node["textures"]:
                    try:  # on first exception assume we have all the ones needed
                        for i in range(1000):
                            full_text_url = texture["urlTemplate"].replace("<texture>", f'{i:03d}')
                            crop_to_do = []
                            if texture["quality"] == "high":
                                crop_to_do = ADV_CROP_FETCH
                            for crop in crop_to_do:
                                for x in list(d_range(0, 1, decimal.Decimal(crop["increment"]))):
                                    for y in list(d_range(0, 1, decimal.Decimal(crop["increment"]))):
                                        xs = f'{x}'
                                        ys = f'{y}'
                                        if xs.endswith('.0'):
                                            xs = xs[:-2]
                                        if ys.endswith('.0'):
                                            ys = ys[:-2]
                                        complete_add = f'{crop["start"]}x{xs},y{ys}'
                                        complete_add_file = complete_add.replace("&", "_")
                                        try:
                                            download_file(full_text_url + "&" + complete_add,
                                                         urlparse(full_text_url).path[1:] + complete_add_file + ".jpg")
                                        except:
                                            pass

                            download_file(full_text_url, urlparse(full_text_url).path[1:])
                    except:
                        pass
        except:
            pass
    # Automatic redirect if GET param isn't correct
    injectedjs = 'if (window.location.search != "?m=' + pageid + '") { document.location.search = "?m=' + pageid + '"; }'
    content = r.text.replace(staticbase, ".").replace('"https://cdn-1.matterport.com/',
                                                      '`${window.location.origin}${window.location.pathname}` + "').replace(
        '"https://mp-app-prod.global.ssl.fastly.net/',
        '`${window.location.origin}${window.location.pathname}` + "').replace("window.MP_PREFETCHED_MODELDATA",
                                                                              f"{injectedjs};window.MP_PREFETCHED_MODELDATA").replace(
        '"https://events.matterport.com/', '`${window.location.origin}${window.location.pathname}` + "')
    content = re.sub(r"validUntil\":\s*\"20[\d]{2}-[\d]{2}-[\d]{2}T", "validUntil\":\"2099-01-01T", content)
    with open("index.html", "w", encoding="UTF-8") as f:
        f.write(content)

    print("Downloading static assets...")
    if os.path.exists(
            "js/showcase.js"):  # we want to always fetch showcase.js in case we patch it differently or the patching function starts to not work well run multiple times on itself
        os.replace("js/showcase.js", "js/showcase-bk.js")  # backing up existing showcase file to be safe
    download_assets(staticbase)
    # Patch showcase.js to fix expiration issue and some other changes for local hosting
    patchShowcase()
    print("Downloading model info...")
    download_info(pageid)
    print("Downloading images...")
    download_pics(pageid)
    print("Downloading graph model data...")
    download_graph_models(pageid)
    print(f"Downloading model... access url: {accessurl}")
    download_model(pageid, accessurl)
    os.chdir(page_root_dir)
    open("api/v1/event", 'a').close()
    print("Done!")


def initiate_download(url):
    download_page(get_page_id(url))


def get_page_id(url):
    return url.split("m=")[-1].split("&")[0]


class OurSimpleHTTPRequestHandler(SimpleHTTPRequestHandler):
    def send_error(self, code, message=None):
        if code == 404:
            logging.warning(f'404 error: {self.path} may not be downloading everything right')
        SimpleHTTPRequestHandler.send_error(self, code, message)

    def do_GET(self):
        global SHOWCASE_INTERNAL_NAME
        redirect_msg = None
        orig_request = self.path
        if self.path.startswith("/js/showcase.js") and os.path.exists(f"js/{SHOWCASE_INTERNAL_NAME}"):
            redirect_msg = "using our internal showcase.js file"
            self.path = f"/js/{SHOWCASE_INTERNAL_NAME}"

        if self.path.startswith("/locale/messages/strings_") and not os.path.exists(f".{self.path}"):
            redirect_msg = "original request was for a locale we do not have downloaded"
            self.path = "/locale/strings.json"
        raw_path, _, query = self.path.partition('?')
        if "crop=" in query and raw_path.endswith(".jpg"):
            query_args = urllib.parse.parse_qs(query)
            crop_addition = query_args.get("crop", None)
            if crop_addition is not None:
                crop_addition = f'crop={crop_addition[0]}'
            else:
                crop_addition = ''

            width_addition = query_args.get("width", None)
            if width_addition is not None:
                width_addition = f'width={width_addition[0]}_'
            else:
                width_addition = ''
            test_path = raw_path + width_addition + crop_addition + ".jpg"
            if os.path.exists(f".{test_path}"):
                self.path = test_path
                redirect_msg = "dollhouse/floorplan texture request that we have downloaded, better than generic texture file"
        if redirect_msg is not None or orig_request != self.path:
            logging.info(f'Redirecting {orig_request} => {self.path} as {redirect_msg}')

        SimpleHTTPRequestHandler.do_GET(self)
        return;

    def do_post(self):
        post_msg = None
        try:
            if self.path == "/api/mp/models/graph":
                self.send_response(200)
                self.end_headers()
                content_len = int(self.headers.get('content-length'))
                post_body = self.rfile.read(content_len).decode('utf-8')
                json_body = json.loads(post_body)
                option_name = json_body["operationName"]
                if option_name in GRAPH_DATA_REQ:
                    file_path = f"api/mp/models/graph_{option_name}.json"
                    if os.path.exists(file_path):
                        with open(file_path, "r", encoding="UTF-8") as f:
                            self.wfile.write(f.read().encode('utf-8'))
                            post_msg = f"graph of operationName: {option_name} we are handling internally"
                            return;
                    else:
                        post_msg = f"graph for operationName: {option_name} we don't know how to handle, but likely could add support, returning empty instead"

                self.wfile.write(bytes('{"data": "empty"}', "utf-8"))
                return
        except Exception as error:
            post_msg = f"Error trying to handle a post request of: {str(error)} this should not happen"
            pass
        finally:
            if post_msg is not None:
                logging.info(f'Handling a post request on {self.path}: {post_msg}')

        self.do_GET()  # just treat the POST as a get otherwise:)

    def guess_type(self, path):
        res = SimpleHTTPRequestHandler.guess_type(self, path)
        if res == "text/html":
            return "text/html; charset=UTF-8"
        return res


PROXY = False
ADVANCED_DOWNLOAD_ALL = False
GRAPH_DATA_REQ = {}


def open_dir_read_graph_reqs(path, pageId):
    for root, dirs, filenames in os.walk(path):
        for file in filenames:
            with open(os.path.join(root, file), "r", encoding="UTF-8") as f:
                GRAPH_DATA_REQ[file.replace(".json", "")] = f.read().replace("[MATTERPORT_MODEL_ID]", pageId)


def get_url_opener(use_proxy):
    if use_proxy:
        proxy = urllib.request.ProxyHandler({'http': use_proxy, 'https': use_proxy})
        opener = urllib.request.build_opener(proxy)
    else:
        opener = urllib.request.build_opener()
    opener.addheaders = [('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'),
                         ('x-matterport-application-name', 'showcase')]
    return opener


def get_command_line_arg(name, has_value):
    for i in range(1, len(sys.argv)):
        if sys.argv[i] == name:
            sys.argv.pop(i)
            if has_value:
                return sys.argv.pop(i)
            else:
                return True
    return False


if __name__ == "__main__":
    ADVANCED_DOWNLOAD_ALL = get_command_line_arg("--advanced-download", False)
    PROXY = get_command_line_arg("--proxy", True)
    OUR_OPENER = get_url_opener(PROXY)
    urllib.request.install_opener(OUR_OPENER)
    pageId = ""
    if len(sys.argv) > 1:
        pageId = get_page_id(sys.argv[1])
    open_dir_read_graph_reqs("graph_posts", pageId)
    if len(sys.argv) == 2:
        initiate_download(pageId)
    elif len(sys.argv) == 4:
        os.chdir(get_page_id(pageId))
        try:
            logging.basicConfig(filename='server.log', encoding='utf-8', level=logging.DEBUG,
                                format='%(asctime)s %(levelname)-8s %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
        except ValueError:
            logging.basicConfig(filename='server.log', level=logging.DEBUG,
                                format='%(asctime)s %(levelname)-8s %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
        logging.info("Server started up")
        print("View in browser: http://" + sys.argv[2] + ":" + sys.argv[3])
        httpd = HTTPServer((sys.argv[2], int(sys.argv[3])), OurSimpleHTTPRequestHandler)
        httpd.serve_forever()
    else:
        print(f"""Matterport Downloader
        
Archiving:
matterport-dl.py URL_OR_PAGE_ID

Replaying:
matterport-dl.py URL_OR_PAGE_ID 127.0.0.1 8080

Arguments:
--advanced-download \t Use this option to try and download the cropped files for dollhouse/floorplan support
--proxy \t\t\t\t Specifies a proxy (e.g: 127.0.0.1:3128)
""")
