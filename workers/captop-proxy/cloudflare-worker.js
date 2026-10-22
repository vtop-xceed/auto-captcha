/**
 * Cloudflare Worker Proxy for CAPTOP
 * This script forwards requests to captop.duckdns.org to bypass network blocks.
 */

addEventListener('fetch', event => {
    event.respondWith(handleRequest(event.request))
})

async function handleRequest(request) {
    const url = new URL(request.url)
    const targetUrl = 'https://captop.duckdns.org' + url.pathname + url.search

    // Handle preflight requests
    if (request.method === 'OPTIONS') {
        return new Response(null, {
            headers: {
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
                'Access-Control-Allow-Headers': 'Content-Type',
                'Max-Age': '86400',
            },
        })
    }

    // Clone headers
    const headers = new Headers(request.headers)

    const modifiedRequest = new Request(targetUrl, {
        method: request.method,
        headers: headers,
        body: request.method === 'POST' ? request.body : null,
        redirect: 'follow'
    })

    try {
        const response = await fetch(modifiedRequest)

        // Add CORS headers to the response
        const newHeaders = new Headers(response.headers)
        newHeaders.set('Access-Control-Allow-Origin', '*')

        return new Response(response.body, {
            status: response.status,
            statusText: response.statusText,
            headers: newHeaders
        })
    } catch (err) {
        return new Response('Proxy error: ' + err.message, { status: 500 })
    }
}
