(() => {
    "use strict";

    const groups = new Map();
    document.querySelectorAll("[data-company-url]").forEach((element) => {
        const organization = (element.dataset.companyOrganization || "").trim();
        const key = /^[0-9]{6}-?[0-9]{4}$/.test(organization)
            ? `organization:${organization.replace("-", "")}`
            : `booking:${element.dataset.companyUrl}`;
        if (!groups.has(key)) {
            groups.set(key, []);
        }
        groups.get(key).push(element);
    });

    const messages = {
        loading: "Hämtar företagsnamn…",
        not_found: "Företagsnamn hittades inte",
        invalid: "Organisationsnumret kan inte slås upp",
        unavailable: "Företagsnamnet kunde inte hämtas just nu",
    };
    const show = (elements, status, name) => {
        elements.forEach((element) => {
            element.textContent = status === "found" ? name : messages[status];
            element.dataset.companyState = status;
            element.classList.toggle("workflow-muted", status !== "found");
        });
    };

    const queue = Array.from(groups.values());
    const lookup = async (elements) => {
        show(elements, "loading");
        const controller = new AbortController();
        const timeout = window.setTimeout(() => controller.abort(), 10000);
        try {
            const response = await fetch(elements[0].dataset.companyUrl, {
                credentials: "same-origin",
                mode: "same-origin",
                redirect: "error",
                headers: { Accept: "application/json" },
                signal: controller.signal,
            });
            if (!response.ok) {
                throw new Error("Company lookup failed");
            }
            const result = await response.json();
            if (result.status === "found" && typeof result.name === "string" && result.name.trim()) {
                show(elements, "found", result.name.trim());
            } else if (["not_found", "invalid", "unavailable"].includes(result.status)) {
                show(elements, result.status);
            } else {
                throw new Error("Invalid company lookup response");
            }
        } catch {
            show(elements, "unavailable");
        } finally {
            window.clearTimeout(timeout);
        }
    };

    // Limit concurrent requests and look up a repeated organisation only once per page.
    const worker = async () => {
        while (queue.length) {
            await lookup(queue.shift());
        }
    };
    for (let i = 0; i < Math.min(2, groups.size); i += 1) {
        worker();
    }
})();
