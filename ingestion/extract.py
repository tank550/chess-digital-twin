import httpx
import json
import os
from dotenv import load_dotenv

load_dotenv()

async def url_check(url: str, timeout: int = 10, headers: dict = None) -> dict:
    
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()

    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return False
        elif exc.response.status_code == 304:
            return { "headers": dict(exc.response.headers), "message": f"Data has not changed since the last request | logs:{exc}."}
    
    except httpx.RequestError:
        raise ValueError(f"Failed to connect to {url}. Please check the URL and try again or check your internet connection | logs:{exc}.")

    else:
        return {
            "headers": dict(response.headers),
            "content": response.json()
        }

async def get_chess_archive():
    
    ARCHIVES_URL = str(os.getenv("URL_CHESS_COM"))+ "/games/archives"

    archives = await url_check(ARCHIVES_URL)

    return archives

async def get_newest_chess_games():
    etag: str = 'W/"9bcad462b064393df9174f448367b9e2"'
    archives = await url_check(str(os.getenv("URL_CHESS_COM"))+ "/games/archives", headers={"If-None-Match": etag})

    return archives
if __name__ == "__main__":
    import asyncio
    #print(asyncio.run(get_chess_archive()))
    print(asyncio.run(get_newest_chess_games()))