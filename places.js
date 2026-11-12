const button = document.querySelector("#watch");
const status = document.querySelector("#status");
const places = [...document.querySelectorAll("[data-place]")];
const inside = new Set();
let watch = null;

function distance(lat1, lon1, lat2, lon2) {
    const rad = Math.PI / 180;
    const a = Math.sin((lat2 - lat1) * rad / 2) ** 2
        + Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin((lon2 - lon1) * rad / 2) ** 2;
    return 12742000 * Math.asin(Math.sqrt(Math.min(1, a)));
}

button.addEventListener("click", () => {
    if (watch !== null) {
        navigator.geolocation.clearWatch(watch);
        watch = null;
        inside.clear();
        button.textContent = "Start arrival checks";
        status.textContent = "Arrival checks stopped.";
        return;
    }
    if (!navigator.geolocation || !places.length) {
        status.textContent = "Add a place and open this page over HTTPS or localhost.";
        return;
    }
    if ("Notification" in window && Notification.permission === "default") {
        Notification.requestPermission().catch(() => {});
    }
    status.textContent = "Waiting for your phone to report its location…";
    watch = navigator.geolocation.watchPosition(({coords}) => {
        const arrivals = [];
        for (const place of places) {
            const {place: id, lat, lon, radius} = place.dataset;
            const meters = distance(coords.latitude, coords.longitude, Number(lat), Number(lon));
            if (meters + coords.accuracy <= Number(radius) && !inside.has(id)) {
                inside.add(id);
                const label = place.querySelector("strong").textContent;
                arrivals.push(label);
                if ("Notification" in window && Notification.permission === "granted") {
                    try { new Notification("Commander Calendar", {body: label}); } catch {}
                }
            } else if (meters - coords.accuracy > Number(radius)) {
                inside.delete(id);
            }
        }
        if (arrivals.length) status.textContent = arrivals.join(" · ");
        else if (status.textContent.startsWith("Waiting")) status.textContent = "Checking for arrivals.";
    }, error => {
        status.textContent = error.message;
    }, {enableHighAccuracy: true, maximumAge: 10000, timeout: 20000});
    button.textContent = "Stop arrival checks";
});

const locate = document.querySelector("#locate");
const locationStatus = document.querySelector("#location-status");
const coordinates = document.querySelector("#place-coordinates");
const latitude = coordinates.querySelector("[name=lat]");
const longitude = coordinates.querySelector("[name=lon]");
const radius = document.querySelector("[name=radius]");

locate.addEventListener("click", () => {
    if (!window.isSecureContext || !navigator.geolocation) {
        locationStatus.textContent = "Open the calendar over HTTPS to use your location.";
        coordinates.open = true;
        return;
    }
    locate.disabled = true;
    locationStatus.textContent = "Getting your location…";
    navigator.geolocation.getCurrentPosition(({coords}) => {
        latitude.value = coords.latitude.toFixed(6);
        longitude.value = coords.longitude.toFixed(6);
        const accuracy = Math.ceil(coords.accuracy);
        const warning = accuracy >= Number(radius.value) ? " Try again outdoors or increase the arrival radius." : "";
        locationStatus.textContent = `Location ready. Estimated accuracy: ${accuracy} m.${warning}`;
        locate.disabled = false;
    }, error => {
        const messages = {
            1: "Location permission was denied. Enable it in your browser or enter coordinates manually.",
            2: "Your phone could not get a location. Try again outdoors or enter coordinates manually.",
            3: "Location lookup timed out. Try again or enter coordinates manually."
        };
        locationStatus.textContent = messages[error.code] || "Location unavailable.";
        locate.disabled = false;
        coordinates.open = true;
    }, {enableHighAccuracy: false, maximumAge: 30000, timeout: 45000});
});

for (const input of [latitude, longitude]) {
    input.addEventListener("invalid", () => { coordinates.open = true; });
}
