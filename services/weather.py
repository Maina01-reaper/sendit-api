import httpx
import os
from typing import Optional, Dict

WEATHER_API_URL = os.getenv("WEATHER_API_URL", "https://api.open-meteo.com/v1/forecast")
GEOCODING_API_URL = "https://geocoding-api.open-meteo.com/v1/search"


async def get_coordinates(city: str, country: str = "Kenya") -> Optional[tuple]:
    """Get latitude and longitude for a city using Open-Meteo geocoding."""
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                GEOCODING_API_URL,
                params={"name": city, "count": 1, "language": "en", "format": "json"},
                timeout=10.0,
            )
            response.raise_for_status()
            data = response.json()

            if data.get("results"):
                result = data["results"][0]
                return (result["latitude"], result["longitude"])
        except Exception as e:
            print(f"Geocoding error: {e}")
        return None


async def get_weather(city: str, country: str = "Kenya") -> Optional[Dict]:
    """Fetch current weather for a city using Open-Meteo API."""
    coordinates = await get_coordinates(city, country)
    if not coordinates:
        return {"error": "Could not find city coordinates"}

    lat, lon = coordinates

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                WEATHER_API_URL,
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "current_weather": True,
                    "temperature_unit": "celsius",
                    "timezone": "Africa/Nairobi",
                },
                timeout=10.0,
            )
            response.raise_for_status()
            data = response.json()

            current = data.get("current_weather", {})
            return {
                "city": city,
                "country": country,
                "temperature": current.get("temperature"),
                "windspeed": current.get("windspeed"),
                "weathercode": current.get("weathercode"),
                "time": current.get("time"),
                "source": "Open-Meteo",
            }
        except httpx.TimeoutException:
            print(f"Weather API timeout for {city}")
            return {"error": "Weather API timeout"}
        except Exception as e:
            print(f"Weather API error: {e}")
            return {"error": str(e)}
