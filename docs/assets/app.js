const API_BASE =
    "https://services.arcgis.com/Sv9ZXFjH5h1fYAaI/arcgis/rest/services/Missing_Children_Cases_View_Master/FeatureServer/0";

const QUERY_FIELDS = [
    "objectid",
    "globalid",
    "status",
    "casestatus",
    "name",
    "age",
    "gender",
    "ethnicity",
    "city",
    "province",
    "missing",
    "description",
    "authname",
    "authemail",
    "authlink",
    "authphone",
    "authphonetwo",
    "thumb_url",
    "mcscemail",
    "mcscphone",
    "CreationDate",
    "EditDate",
];

const PROVINCE_LABELS = {
    Alberta: "Alberta",
    BritishColumbia: "British Columbia",
    Manitoba: "Manitoba",
    NewBrunswick: "New Brunswick",
    NewfoundlandandLabrador: "Newfoundland and Labrador",
    NT: "Northwest Territories",
    NovaScotia: "Nova Scotia",
    NU: "Nunavut",
    Ontario: "Ontario",
    PrinceEdwardIsland: "Prince Edward Island",
    Quebec: "Quebec",
    Saskatchewan: "Saskatchewan",
    YT: "Yukon",
};

const STATUS_LABELS = {
    missing: "Missing",
    vulnerable: "Vulnerable",
    abudction: "Abduction",
    amberalert: "Amber Alert",
    childsearchalert: "Child Search Alert",
};

const state = {
    cases: [],
    filteredCases: [],
    selectedCaseId: null,
    map: null,
    markersLayer: null,
    attachmentCache: new Map(),
};

const elements = {
    feedStatus: document.getElementById("feedStatus"),
    statTotal: document.getElementById("statTotal"),
    statHighRisk: document.getElementById("statHighRisk"),
    statRecent: document.getElementById("statRecent"),
    statUpdated: document.getElementById("statUpdated"),
    resultsCount: document.getElementById("resultsCount"),
    provinceStrip: document.getElementById("provinceStrip"),
    caseList: document.getElementById("caseList"),
    provinceSelect: document.getElementById("provinceSelect"),
    statusSelect: document.getElementById("statusSelect"),
    sortSelect: document.getElementById("sortSelect"),
    searchInput: document.getElementById("searchInput"),
    fitMapButton: document.getElementById("fitMapButton"),
    detailEmpty: document.getElementById("detailEmpty"),
    detailPanel: document.getElementById("detailPanel"),
    detailStatus: document.getElementById("detailStatus"),
    detailMeta: document.getElementById("detailMeta"),
    detailName: document.getElementById("detailName"),
    detailLocation: document.getElementById("detailLocation"),
    detailSummary: document.getElementById("detailSummary"),
    detailGallery: document.getElementById("detailGallery"),
    detailGrid: document.getElementById("detailGrid"),
    detailDescription: document.getElementById("detailDescription"),
    authorityLink: document.getElementById("authorityLink"),
    mcscEmailLink: document.getElementById("mcscEmailLink"),
    authorityPhoneLink: document.getElementById("authorityPhoneLink"),
};

document.addEventListener("DOMContentLoaded", () => {
    initMap();
    bindControls();
    loadCases();
});

function initMap() {
    state.map = L.map("map", {
        center: [56.1304, -106.3468],
        zoom: 4,
        scrollWheelZoom: true,
    });

    L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
        attribution:
            '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; CARTO',
        subdomains: "abcd",
        maxZoom: 19,
    }).addTo(state.map);

    state.markersLayer = L.layerGroup().addTo(state.map);
}

function bindControls() {
    elements.searchInput.addEventListener("input", applyFilters);
    elements.provinceSelect.addEventListener("change", applyFilters);
    elements.statusSelect.addEventListener("change", applyFilters);
    elements.sortSelect.addEventListener("change", applyFilters);
    elements.fitMapButton.addEventListener("click", fitMapToFilteredCases);
}

async function loadCases() {
    try {
        setFeedStatus("Loading live feed", false);

        const params = new URLSearchParams({
            where: "casestatus='open'",
            outFields: QUERY_FIELDS.join(","),
            returnGeometry: "true",
            orderByFields: "missing DESC",
            resultRecordCount: "1000",
            f: "json",
        });

        const response = await fetch(`${API_BASE}/query?${params.toString()}`);
        if (!response.ok) {
            throw new Error(`Feed returned ${response.status}`);
        }

        const payload = await response.json();
        state.cases = (payload.features || [])
            .map(normalizeCase)
            .filter((item) => item.caseStatus === "open");

        populateProvinceOptions(state.cases);
        renderStats(state.cases);
        applyFilters();
        setFeedStatus("Live feed connected", true);
    } catch (error) {
        console.error(error);
        elements.caseList.innerHTML =
            '<div class="empty-state">Unable to load the live public data feed right now.</div>';
        setFeedStatus("Feed unavailable", false);
    }
}

function normalizeCase(feature) {
    const attrs = feature.attributes || {};
    const geometry = feature.geometry || {};
    const missingDate = attrs.missing ? new Date(attrs.missing) : null;
    const updatedDate = attrs.EditDate ? new Date(attrs.EditDate) : null;

    return {
        id: attrs.objectid,
        name: attrs.name || "Name unavailable",
        age: isFiniteNumber(attrs.age) ? attrs.age : null,
        gender: attrs.gender || "",
        ethnicity: attrs.ethnicity || "",
        city: attrs.city || "",
        province: attrs.province || "",
        provinceLabel: PROVINCE_LABELS[attrs.province] || attrs.province || "Unknown province",
        status: attrs.status || "missing",
        statusLabel: STATUS_LABELS[attrs.status] || "Missing",
        caseStatus: attrs.casestatus || "",
        descriptionHtml: attrs.description || "",
        authorityName: attrs.authname || "",
        authorityEmail: attrs.authemail || "",
        authorityLink: attrs.authlink || "",
        authorityPhone: attrs.authphone || "",
        authorityPhoneAlt: attrs.authphonetwo || "",
        mcscEmail: attrs.mcscemail || "tips@mcsc.ca",
        mcscPhone: attrs.mcscphone || "",
        thumbUrl: attrs.thumb_url || "",
        missingDate,
        updatedDate,
        latitude: geometry.y ?? null,
        longitude: geometry.x ?? null,
    };
}

function populateProvinceOptions(cases) {
    const provinces = [...new Set(cases.map((item) => item.province).filter(Boolean))]
        .sort((a, b) => (PROVINCE_LABELS[a] || a).localeCompare(PROVINCE_LABELS[b] || b));

    elements.provinceSelect.innerHTML = '<option value="">All provinces</option>';
    for (const province of provinces) {
        const option = document.createElement("option");
        option.value = province;
        option.textContent = PROVINCE_LABELS[province] || province;
        elements.provinceSelect.appendChild(option);
    }
}

function renderStats(cases) {
    const now = Date.now();
    const recentThreshold = now - 30 * 24 * 60 * 60 * 1000;
    const highRiskStatuses = new Set(["vulnerable", "amberalert", "abudction", "childsearchalert"]);

    const total = cases.length;
    const highRisk = cases.filter((item) => highRiskStatuses.has(item.status)).length;
    const recent = cases.filter((item) => item.missingDate && item.missingDate.getTime() >= recentThreshold).length;
    const latestUpdate = cases
        .map((item) => item.updatedDate)
        .filter(Boolean)
        .sort((a, b) => b - a)[0];

    elements.statTotal.textContent = String(total);
    elements.statHighRisk.textContent = String(highRisk);
    elements.statRecent.textContent = String(recent);
    elements.statUpdated.textContent = latestUpdate ? formatDate(latestUpdate) : "Unavailable";

    const byProvince = countBy(cases, (item) => item.provinceLabel);
    const topProvinces = [...byProvince.entries()]
        .sort((a, b) => b[1] - a[1])
        .slice(0, 6);

    elements.provinceStrip.innerHTML = "";
    for (const [province, count] of topProvinces) {
        const chip = document.createElement("span");
        chip.className = "province-chip";
        chip.textContent = `${province} ${count}`;
        elements.provinceStrip.appendChild(chip);
    }
}

function applyFilters() {
    const query = elements.searchInput.value.trim().toLowerCase();
    const province = elements.provinceSelect.value;
    const status = elements.statusSelect.value;
    const sortMode = elements.sortSelect.value;

    let results = state.cases.filter((item) => {
        const matchesQuery =
            !query ||
            [item.name, item.city, item.provinceLabel, item.statusLabel]
                .join(" ")
                .toLowerCase()
                .includes(query);
        const matchesProvince = !province || item.province === province;
        const matchesStatus = !status || item.status === status;
        return matchesQuery && matchesProvince && matchesStatus;
    });

    results = sortCases(results, sortMode);
    state.filteredCases = results;

    renderCaseList(results);
    renderMarkers(results);
    updateResultsCount(results.length);

    if (!results.some((item) => item.id === state.selectedCaseId)) {
        if (results[0]) {
            selectCase(results[0].id);
        } else {
            clearDetailPanel();
        }
    } else {
        renderActiveCard();
    }
}

function sortCases(cases, mode) {
    const sorted = [...cases];
    sorted.sort((a, b) => {
        switch (mode) {
            case "missing-asc":
                return compareDates(a.missingDate, b.missingDate);
            case "age-asc":
                return compareNumbers(a.age, b.age);
            case "age-desc":
                return compareNumbers(b.age, a.age);
            case "name-asc":
                return a.name.localeCompare(b.name);
            case "missing-desc":
            default:
                return compareDates(b.missingDate, a.missingDate);
        }
    });
    return sorted;
}

function renderCaseList(cases) {
    if (!cases.length) {
        elements.caseList.innerHTML =
            '<div class="empty-state">No active cases match these filters right now.</div>';
        return;
    }

    elements.caseList.innerHTML = "";
    const fragment = document.createDocumentFragment();

    for (const item of cases) {
        const card = document.createElement("article");
        card.className = "case-card";
        card.dataset.caseId = String(item.id);
        card.innerHTML = `
            <div class="case-card-media">
                ${item.thumbUrl
                    ? `<img src="${item.thumbUrl}" alt="${escapeHtml(item.name)}" loading="lazy">`
                    : ""}
            </div>
            <div>
                <div class="case-card-title">
                    <div>
                        <h4>${escapeHtml(item.name)}</h4>
                        <p>${escapeHtml(item.city || "Location unavailable")}, ${escapeHtml(item.provinceLabel)}</p>
                    </div>
                    <span class="status-tag status-${item.status}">${escapeHtml(item.statusLabel)}</span>
                </div>
                <p>Age ${item.age ?? "Unknown"}${item.gender ? ` · ${capitalize(item.gender)}` : ""}</p>
                <p>Missing since ${item.missingDate ? formatDate(item.missingDate) : "Unknown"}</p>
            </div>
        `;
        card.addEventListener("click", () => selectCase(item.id, true));
        fragment.appendChild(card);
    }

    elements.caseList.appendChild(fragment);
    renderActiveCard();
}

function renderMarkers(cases) {
    state.markersLayer.clearLayers();

    const bounds = [];
    for (const item of cases) {
        if (!isFiniteNumber(item.latitude) || !isFiniteNumber(item.longitude)) {
            continue;
        }

        const marker = L.marker([item.latitude, item.longitude], {
            icon: L.divIcon({
                className: "",
                html: `<span class="marker-dot marker-${item.status}"></span>`,
                iconSize: [18, 18],
                iconAnchor: [9, 9],
            }),
            title: item.name,
        });

        marker.bindPopup(`
            <strong>${escapeHtml(item.name)}</strong><br>
            ${escapeHtml(item.city || "Unknown city")}, ${escapeHtml(item.provinceLabel)}<br>
            ${escapeHtml(item.statusLabel)}<br>
            Missing since ${item.missingDate ? formatDate(item.missingDate) : "Unknown"}
        `);

        marker.on("click", () => selectCase(item.id, true));
        marker.addTo(state.markersLayer);
        bounds.push([item.latitude, item.longitude]);
    }

    if (bounds.length) {
        state.map.fitBounds(L.latLngBounds(bounds).pad(0.2), { maxZoom: 6 });
    }
}

function fitMapToFilteredCases() {
    const points = state.filteredCases
        .filter((item) => isFiniteNumber(item.latitude) && isFiniteNumber(item.longitude))
        .map((item) => [item.latitude, item.longitude]);

    if (!points.length) {
        return;
    }

    state.map.fitBounds(L.latLngBounds(points).pad(0.2), { maxZoom: 6 });
}

async function selectCase(caseId, flyToMarker = false) {
    const item = state.filteredCases.find((entry) => entry.id === caseId) || state.cases.find((entry) => entry.id === caseId);
    if (!item) {
        return;
    }

    state.selectedCaseId = caseId;
    renderActiveCard();

    if (flyToMarker && isFiniteNumber(item.latitude) && isFiniteNumber(item.longitude)) {
        state.map.flyTo([item.latitude, item.longitude], 8, { duration: 0.8 });
    }

    elements.detailEmpty.classList.add("hidden");
    elements.detailPanel.classList.remove("hidden");
    elements.detailStatus.className = `status-tag status-${item.status}`;
    elements.detailStatus.textContent = item.statusLabel;
    elements.detailMeta.textContent = item.updatedDate ? `Updated ${formatDate(item.updatedDate)}` : "Live public record";
    elements.detailName.textContent = item.name;
    elements.detailLocation.textContent = `${item.city || "Location unavailable"}, ${item.provinceLabel}`;
    elements.detailSummary.textContent = item.missingDate
        ? `Missing since ${formatDate(item.missingDate)}`
        : "Missing date unavailable";
    elements.detailDescription.innerHTML = item.descriptionHtml || "<p>No additional public description was provided.</p>";

    renderDetailGrid(item);
    renderActionLinks(item);
    await renderGallery(item);
}

function renderDetailGrid(item) {
    const rows = [
        ["Age", item.age ?? "Unknown"],
        ["Gender", item.gender ? capitalize(item.gender) : "Unknown"],
        ["Ethnicity", item.ethnicity && item.ethnicity !== "notlisted" ? capitalize(item.ethnicity) : "Not listed"],
        ["Authority", item.authorityName || "Not listed"],
        ["Authority Phone", item.authorityPhone || item.authorityPhoneAlt || "Not listed"],
        ["MCSC Tips", item.mcscPhone || item.mcscEmail || "Not listed"],
    ];

    elements.detailGrid.innerHTML = rows
        .map(
            ([label, value]) => `
                <div>
                    <dt>${escapeHtml(label)}</dt>
                    <dd>${escapeHtml(String(value))}</dd>
                </div>
            `
        )
        .join("");
}

async function renderGallery(item) {
    elements.detailGallery.innerHTML = item.thumbUrl
        ? `<img src="${item.thumbUrl}" alt="${escapeHtml(item.name)}" loading="lazy">`
        : "";

    const attachments = await fetchAttachments(item.id);
    if (!attachments.length) {
        return;
    }

    elements.detailGallery.innerHTML = attachments
        .slice(0, 6)
        .map((attachment) => {
            const url = `${API_BASE}/${item.id}/attachments/${attachment.id}`;
            return `<img src="${url}" alt="${escapeHtml(item.name)}" loading="lazy">`;
        })
        .join("");
}

async function fetchAttachments(caseId) {
    if (state.attachmentCache.has(caseId)) {
        return state.attachmentCache.get(caseId);
    }

    try {
        const response = await fetch(`${API_BASE}/${caseId}/attachments?f=json`);
        if (!response.ok) {
            throw new Error(`Attachment request returned ${response.status}`);
        }

        const payload = await response.json();
        const attachments = payload.attachmentInfos || [];
        state.attachmentCache.set(caseId, attachments);
        return attachments;
    } catch (error) {
        console.warn("Attachment fetch failed", error);
        state.attachmentCache.set(caseId, []);
        return [];
    }
}

function renderActionLinks(item) {
    if (item.authorityLink) {
        elements.authorityLink.href = item.authorityLink;
        elements.authorityLink.style.display = "inline-flex";
    } else {
        elements.authorityLink.style.display = "none";
    }

    const email = item.mcscEmail || item.authorityEmail;
    if (email) {
        elements.mcscEmailLink.href = `mailto:${email}`;
        elements.mcscEmailLink.textContent = email === item.mcscEmail ? "Email MCSC tips" : "Email listed authority";
        elements.mcscEmailLink.style.display = "inline-flex";
    } else {
        elements.mcscEmailLink.style.display = "none";
    }

    const phone = item.authorityPhone || item.authorityPhoneAlt || item.mcscPhone;
    if (phone) {
        elements.authorityPhoneLink.href = `tel:${phone.replace(/\s+/g, "")}`;
        elements.authorityPhoneLink.textContent = `Call ${phone.trim()}`;
        elements.authorityPhoneLink.style.display = "inline-flex";
    } else {
        elements.authorityPhoneLink.style.display = "none";
    }
}

function clearDetailPanel() {
    state.selectedCaseId = null;
    elements.detailPanel.classList.add("hidden");
    elements.detailEmpty.classList.remove("hidden");
    renderActiveCard();
}

function renderActiveCard() {
    document.querySelectorAll(".case-card").forEach((card) => {
        card.classList.toggle("active", Number(card.dataset.caseId) === state.selectedCaseId);
    });
}

function updateResultsCount(count) {
    elements.resultsCount.textContent = `${count} result${count === 1 ? "" : "s"}`;
}

function setFeedStatus(text, healthy) {
    elements.feedStatus.textContent = text;
    elements.feedStatus.style.color = healthy ? "var(--fresh)" : "var(--warning)";
}

function countBy(items, getKey) {
    const counts = new Map();
    for (const item of items) {
        const key = getKey(item);
        counts.set(key, (counts.get(key) || 0) + 1);
    }
    return counts;
}

function compareDates(a, b) {
    const left = a instanceof Date ? a.getTime() : -Infinity;
    const right = b instanceof Date ? b.getTime() : -Infinity;
    return left - right;
}

function compareNumbers(a, b) {
    const left = Number.isFinite(a) ? a : Infinity;
    const right = Number.isFinite(b) ? b : Infinity;
    return left - right;
}

function formatDate(date) {
    return new Intl.DateTimeFormat("en-CA", {
        year: "numeric",
        month: "short",
        day: "numeric",
    }).format(date);
}

function escapeHtml(value) {
    return String(value)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/\"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

function capitalize(value) {
    if (!value) {
        return "";
    }
    return value.charAt(0).toUpperCase() + value.slice(1);
}

function isFiniteNumber(value) {
    return typeof value === "number" && Number.isFinite(value);
}
