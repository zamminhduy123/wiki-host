"""
routers/ollama_proxy.py — Proxy router for exposing local Ollama instance via FastAPI.
"""
from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse
import httpx
import logging

from config import settings

router = APIRouter()
logger = logging.getLogger(__name__)

async def proxy_to_ollama(request: Request, path: str):
    method = request.method
    full_url = f"{settings.ollama_base_url.rstrip('/')}/{path}"
    
    logger.info(f"Ollama Proxy: {method} {request.url.path} -> {full_url}")

    # Forward the query params
    params = dict(request.query_params)
    
    # Forward headers SAFELY
    headers = {
        key: value 
        for key, value in request.headers.items() 
        if key.lower() not in ("host", "content-length")
    }

    # Ollama could take a long time to generate a response
    client = httpx.AsyncClient(timeout=300.0) 
    
    body = await request.body() if method in ("POST", "PUT", "PATCH", "DELETE") else None
    
    try:
        req = client.build_request(
            method=method,
            url=full_url,
            headers=headers,
            content=body,
            params=params
        )
        
        response = await client.send(req, stream=True)
        logger.info(f"Ollama Response: {response.status_code} for {path}")
        
        async def stream_generator():
            try:
                async for chunk in response.aiter_raw():
                    yield chunk
            finally:
                await response.aclose()
                await client.aclose()
                logger.info(f"Ollama Stream Closed for {path}")

        return StreamingResponse(
            stream_generator(),
            status_code=response.status_code,
            headers={
                k: v for k, v in response.headers.items() 
                if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")
            }
        )
    except Exception as e:
        await client.aclose()
        logger.error(f"Ollama Proxy Error at {path}: {str(e)}")
        return Response(content=f"Ollama Proxy Error: {str(e)}", status_code=500)

@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
async def handle_ollama(request: Request, path: str):
    """
    Proxies all requests to the local Ollama instance.
    """
    return await proxy_to_ollama(request, path)
