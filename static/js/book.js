(() => {
    "use strict";

    const form = document.getElementById("booking-form");
    if (!form) {
        return;
    }

    const setConditionalFields = (container, active, clearInactive) => {
        if (!container) {
            return;
        }

        container.hidden = !active;
        const controls = container.querySelectorAll("input, select, textarea");
        controls.forEach((control) => {
            control.disabled = !active;
            if (control.dataset.requiredWhenActive === "true") {
                control.required = active;
            }
            if (!active && clearInactive) {
                control.value = "";
            }
        });
    };

    const selectedRadioValue = (name) => {
        const selected = form.querySelector(`input[name="${name}"]:checked`);
        return selected ? selected.value : "";
    };

    const exactDateFields = document.getElementById("exact-date-fields");
    const dateRangeFields = document.getElementById("date-range-fields");

    const updateDateMode = (clearInactive) => {
        const mode = selectedRadioValue("datumtyp");
        setConditionalFields(exactDateFields, mode === "exakt", clearInactive);
        setConditionalFields(dateRangeFields, mode === "intervall", clearInactive);
    };

    form.querySelectorAll('input[name="datumtyp"]').forEach((radio) => {
        radio.addEventListener("change", () => updateDateMode(true));
    });

    const catalogCourseFields = document.getElementById("catalog-course-fields");
    const customCourseFields = document.getElementById("custom-course-fields");
    const courseSearch = document.getElementById("utbildning-sok");
    const courseSelect = document.getElementById("utbildning");
    const courseSearchStatus = document.getElementById("utbildning-sok-status");

    const catalogCourses = courseSelect
        ? Array.from(courseSelect.options)
            .filter((option) => option.value)
            .map((option) => ({ value: option.value, label: option.textContent }))
        : [];

    const normalizeSearchText = (value) => String(value)
        .normalize("NFC")
        .toLocaleLowerCase("sv-SE")
        .trim();

    const filterCatalogCourses = () => {
        if (!courseSearch || !courseSelect || !courseSearchStatus) {
            return;
        }

        const query = normalizeSearchText(courseSearch.value);
        const selectedValue = courseSelect.value;
        const matches = catalogCourses.filter((course) => (
            normalizeSearchText(course.label).includes(query)
        ));

        const placeholder = document.createElement("option");
        placeholder.value = "";
        placeholder.textContent = "Välj utbildning";
        placeholder.disabled = true;
        placeholder.selected = !matches.some((course) => course.value === selectedValue);

        const options = matches.map((course) => {
            const option = document.createElement("option");
            option.value = course.value;
            option.textContent = course.label;
            option.selected = course.value === selectedValue;
            return option;
        });

        courseSelect.replaceChildren(placeholder, ...options);
        courseSearchStatus.textContent = matches.length === 0
            ? courseSelect.dataset.noResultsText
            : "";
    };

    const updateCourseMode = (clearInactive) => {
        const mode = selectedRadioValue("utbildningskalla");
        setConditionalFields(catalogCourseFields, mode === "katalog", clearInactive);
        setConditionalFields(customCourseFields, mode === "fritext", clearInactive);
        filterCatalogCourses();
    };

    form.querySelectorAll('input[name="utbildningskalla"]').forEach((radio) => {
        radio.addEventListener("change", () => updateCourseMode(true));
    });
    if (courseSearch) {
        courseSearch.addEventListener("input", filterCatalogCourses);
    }

    const localAddressFields = document.getElementById("local-address-fields");
    const updateVenueResponsibility = (clearInactive) => {
        const responsibility = selectedRadioValue("lokalansvar");
        setConditionalFields(
            localAddressFields,
            responsibility === "kund",
            clearInactive,
        );
    };

    form.querySelectorAll('input[name="lokalansvar"]').forEach((radio) => {
        radio.addEventListener("change", () => updateVenueResponsibility(true));
    });

    updateDateMode(false);
    updateCourseMode(false);
    updateVenueResponsibility(false);
})();
