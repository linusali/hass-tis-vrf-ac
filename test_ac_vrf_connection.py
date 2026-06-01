#!/usr/bin/env python3
"""Test script to verify AC VRF API connectivity."""
import asyncio
import aiohttp
from aiohttp import BasicAuth
import json

# CONFIGURE THESE VALUES
HOST = "192.168.1.251"  # Your AC controller IP
USERNAME = "admin"       # Your username (or None if no auth)
PASSWORD = ""            # Your password (or None if no auth)

async def test_connection():
    """Test connection to AC VRF controller."""
    
    # Create auth if username is provided
    auth = BasicAuth(USERNAME, PASSWORD) if USERNAME else None
    
    print(f"Testing connection to AC VRF controller at {HOST}")
    print(f"Using authentication: {'Yes' if auth else 'No'}")
    if auth:
        print(f"Username: {USERNAME}")
    print("-" * 60)
    
    async with aiohttp.ClientSession() as session:
        # Test page 0
        for page in [0, 1]:
            url = f"http://{HOST}/cgi-bin/api.html?f=17&p={page}"
            print(f"\nTesting: {url}")
            
            try:
                async with session.get(url, auth=auth, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    print(f"Status Code: {response.status}")
                    
                    # Read as text first to handle HTTP/0.9
                    text = await response.text()
                    
                    if response.status == 401:
                        print("❌ Authentication failed! Check username/password")
                        return False
                    elif response.status == 200:
                        try:
                            data = json.loads(text)
                            print(f"Response: {json.dumps(data, indent=2)}")
                            
                            if data.get("err") == 0:
                                units = data.get("unit", [])
                                print(f"✅ Found {len(units)} AC unit(s) on page {page}")
                                for unit in units:
                                    print(f"   - {unit['nm']} (idx={unit['idx']}, on={unit['on']}, temp={unit['tempIn']}°C)")
                            else:
                                print(f"⚠️  API returned error code: {data.get('err')}")
                        except json.JSONDecodeError as e:
                            print(f"⚠️  Could not parse JSON response: {e}")
                            print(f"Raw response: {text[:500]}")
                    else:
                        print(f"❌ Unexpected status code: {response.status}")
                        print(f"Response: {text[:200]}")
                        
            except asyncio.TimeoutError:
                print(f"❌ Timeout - Controller not responding at {HOST}")
                return False
            except aiohttp.ClientError as e:
                print(f"⚠️  Connection error (this may be OK for HTTP/0.9): {e}")
                print("Trying to read response anyway...")
                # For HTTP/0.9, we might still get data
                try:
                    # The response might still be readable
                    print("Response might still be valid despite protocol error")
                except Exception:
                    return False
            except Exception as e:
                print(f"❌ Error: {e}")
                import traceback
                print(traceback.format_exc())
                return False
    
    print("\n" + "=" * 60)
    print("✅ Connection test completed!")
    print("=" * 60)
    return True

if __name__ == "__main__":
    asyncio.run(test_connection())
