/**
 * CLOUDFLARE WORKER PROXY FOR CAPTOP
 * 
 * Purpose: Acts as a middleman between the user and PythonAnywhere
 * to bypass institutional firewall blocks.
 */

const ORIGIN_URL = "sykik.pythonanywhere.com";

async function handleRequest(request) {
    const url = new URL(request.url);

    // 1. Change the hostname to our PythonAnywhere backend
    const targetUrl = `https://${ORIGIN_URL}${url.pathname}${url.search}`;

    // 2. Clone the request but change the Host header
    const modifiedRequest = new Request(targetUrl, {
        method: request.method,
        headers: new Headers(request.headers),
        body: request.body,
        redirect: 'manual'
    });

    // Important: PythonAnywhere uses the Host header to route requests
    modifiedRequest.headers.set("Host", ORIGIN_URL);

    // 3. Fetch from the backend
    let response = await fetch(modifiedRequest);

    // 4. Handle Cookies (Re-bind them to the current domain)
    // This is crucial so session tracking keeps working
    const newResponseHeaders = new Headers(response.headers);
    const setCookie = response.headers.get("Set-Cookie");
    if (setCookie) {
        // We don't need to do much here if we are a transparent proxy,
        // as the browser will associate the cookie with the Workers domain.
    }

    // Allow all origins (CORS) as an extra safety measure for your contributors
    newResponseHeaders.set("Access-Control-Allow-Origin", "*");
    newResponseHeaders.set("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
    newResponseHeaders.set("Access-Control-Allow-Headers", "Content-Type");

    return new Response(response.body, {
        status: response.status,
        statusText: response.statusText,
        headers: newResponseHeaders,
    });
}

addEventListener("fetch", (event) => {
    event.respondWith(handleRequest(event.request));
});
