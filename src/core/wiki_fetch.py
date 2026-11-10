"""Read-only public request worker. No browser challenge or access bypass."""
import sys,json,base64
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from urllib.parse import urlsplit


def fetch(url,limit):
    if urlsplit(url).scheme!='https' or urlsplit(url).hostname!='deadbydaylight.wiki.gg': raise ValueError('foreign_source')
    request=Request(url,headers={'User-Agent':'AutoBloodWeb/4.0 (public image library)'})
    with urlopen(request,timeout=20) as response:
        if urlsplit(response.url).hostname!='deadbydaylight.wiki.gg': raise ValueError('foreign_redirect')
        body=response.read(limit+1)
        if len(body)>limit: raise ValueError('wiki_response_too_large')
        return dict(status=response.status,body=base64.b64encode(body).decode('ascii'),
                    content_type=response.headers.get('Content-Type',''),effective=response.url)


if __name__=='__main__':
    try: packet=fetch(sys.argv[1],int(sys.argv[2]))
    except HTTPError as exc: packet=dict(status=exc.code)
    except Exception as exc: packet=dict(error=str(exc))
    print(json.dumps(packet))
