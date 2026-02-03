import asyncio

import httpx


async def send_push_notification(message: str) -> dict:
    """
    Envia notificaciones push.

    Args:
        message: Mensaje a enviar

    Returns:
        dict con el resultado del push
    """

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=20.0)) as client:
            response = await client.post(
                "https://webhook.site/0cf8618c-41ad-4eff-8a8d-e75c25c7bc0c",
                json={"message": message},
            )
            response.raise_for_status()
            try:
                return response.json()
            except httpx.HTTPError as e:
                return {"error": f"Error parsing JSON response: {str(e)}"}
    except httpx.HTTPError as e:
        return {"error": f"HTTP error occurred: {str(e)}"}
    except Exception as e:
        return {"error": f"An unexpected error occurred: {str(e)}"}


# Demo
if __name__ == "__main__":

    async def main():
        query = "promociones en moda"
        print(f"Query: {query}\n")
        result = await send_push_notification(query)
        print(f"Beneficios encontrados: {len(result.get('success', []))}")

    asyncio.run(main())
