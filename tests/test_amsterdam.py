# """Basic tests for the Open Data Platform API of Amsterdam."""
# # pylint: disable=protected-access
# import asyncio
# from unittest.mock import patch

# import aiohttp
# import pytest
# from aresponses import Response, ResponsesMockServer

# from odp_amsterdam import ODPAmsterdam
# from odp_amsterdam.exceptions import ODPAmsterdamConnectionError, ODPAmsterdamError

# from . import load_fixtures


# @pytest.mark.asyncio
# async def test_json_request(aresponses: ResponsesMockServer) -> None:
#     """Test JSON response is handled correctly."""
#     aresponses.add(
#         "api.data.amsterdam.nl",
#         "/test",
#         "GET",
#         aresponses.Response(
#             status=200,
#             headers={"Content-Type": "text/plain"},
#             text=load_fixtures("garages.json"),
#         ),
#     )
#     async with aiohttp.ClientSession() as session:
#         client = ODPAmsterdam(session=session)
#         response = await client._request("test")
#         assert response is not None
#         await client.close()


# @pytest.mark.asyncio
# async def test_internal_session(aresponses: ResponsesMockServer) -> None:
#     """Test internal session is handled correctly."""
#     aresponses.add(
#         "api.data.amsterdam.nl",
#         "/test",
#         "GET",
#         aresponses.Response(
#             status=200,
#             headers={"Content-Type": "text/plain"},
#             text=load_fixtures("garages.json"),
#         ),
#     )
#     async with ODPAmsterdam() as client:
#         await client._request("test")


# @pytest.mark.asyncio
# async def test_timeout(aresponses: ResponsesMockServer) -> None:
#     """Test request timeout from the Open Data Platform API of Amsterdam."""
#     # Faking a timeout by sleeping
#     async def response_handler(_: aiohttp.ClientResponse) -> Response:
#         await asyncio.sleep(0.2)
#         return aresponses.Response(
#             body="Goodmorning!", text=load_fixtures("garages.json")
#         )

#     aresponses.add("api.data.amsterdam.nl", "/test", "GET", response_handler)

#     async with aiohttp.ClientSession() as session:
#         client = ODPAmsterdam(
#             session=session,
#             request_timeout=0.1,
#         )
#         with pytest.raises(ODPAmsterdamConnectionError):
#             assert await client._request("test")


# @pytest.mark.asyncio
# async def test_content_type(aresponses: ResponsesMockServer) -> None:
#     """Test request content type error from Open Data Platform API of Amsterdam."""
#     aresponses.add(
#         "api.data.amsterdam.nl",
#         "/test",
#         "GET",
#         aresponses.Response(
#             status=200,
#             headers={"Content-Type": "blabla/blabla"},
#         ),
#     )

#     async with aiohttp.ClientSession() as session:
#         client = ODPAmsterdam(session=session)
#         with pytest.raises(ODPAmsterdamError):
#             assert await client._request("test")


# @pytest.mark.asyncio
# async def test_client_error() -> None:
#     """Test request client error from the Open Data Platform API of Amsterdam."""
#     async with aiohttp.ClientSession() as session:
#         client = ODPAmsterdam(session=session)
#         with patch.object(
#             session, "request", side_effect=aiohttp.ClientError
#         ), pytest.raises(ODPAmsterdamConnectionError):
#             assert await client._request("test")
