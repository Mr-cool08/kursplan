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
    const coursePicker = document.getElementById("utbildning-valjare");
    const courseList = document.getElementById("utbildning-lista");

    const courseOptions = courseList ? Array.from(courseList.children) : [];
    let visibleCourseOptions = [];
    let activeCourseIndex = -1;

    const normalizeSearchText = (value) => String(value)
        .normalize("NFC")
        .toLocaleLowerCase("sv-SE")
        .trim();

    const closeCourseList = () => {
        if (!courseList || !courseSearch) {
            return;
        }
        courseList.hidden = true;
        courseSearch.setAttribute("aria-expanded", "false");
        courseSearch.removeAttribute("aria-activedescendant");
        courseOptions.forEach((option) => option.setAttribute("aria-selected", "false"));
        activeCourseIndex = -1;
        courseSearchStatus.textContent = "";
    };

    const syncCourseValue = () => {
        const query = normalizeSearchText(courseSearch.value);
        const match = Array.from(courseSelect.options).find((option) => (
            option.value && normalizeSearchText(option.value) === query
        ));
        courseSelect.value = match ? match.value : "";
        courseSearch.setCustomValidity(courseSearch.value && !match ? "Välj en utbildning ur listan." : "");
    };

    const openCourseList = (showAll = false) => {
        const query = showAll ? "" : normalizeSearchText(courseSearch.value);
        visibleCourseOptions = courseOptions.filter((option) => {
            option.hidden = !normalizeSearchText(option.textContent).includes(query);
            option.setAttribute("aria-selected", "false");
            return !option.hidden;
        });
        activeCourseIndex = -1;
        courseSearch.removeAttribute("aria-activedescendant");
        courseList.hidden = visibleCourseOptions.length === 0;
        courseSearch.setAttribute("aria-expanded", String(!courseList.hidden));
        courseSearchStatus.textContent = visibleCourseOptions.length === 0
            ? "Inga utbildningar matchar din sökning."
            : `${visibleCourseOptions.length} ${visibleCourseOptions.length === 1 ? "utbildning hittades" : "utbildningar hittades"}.`;
    };

    const setActiveCourse = (index) => {
        courseOptions.forEach((option) => option.setAttribute("aria-selected", "false"));
        activeCourseIndex = index;
        const option = visibleCourseOptions[index];
        if (option) {
            option.setAttribute("aria-selected", "true");
            courseSearch.setAttribute("aria-activedescendant", option.id);
            option.scrollIntoView({ block: "nearest" });
        }
    };

    const chooseCourse = (option) => {
        courseSearch.value = option.textContent.trim();
        syncCourseValue();
        closeCourseList();
        courseSearch.focus({ preventScroll: true });
        // Focusing after a touch selection may reopen the suggestions.
        closeCourseList();
    };

    if (coursePicker && courseSearch && courseSelect && courseList && courseSearchStatus) {
        courseSearch.value = courseSelect.value || courseSearch.value;
        courseSearch.dataset.requiredWhenActive = "true";
        delete courseSelect.dataset.requiredWhenActive;
        courseSelect.required = false;
        courseSelect.hidden = true;
        coursePicker.hidden = false;
        document.getElementById("utbildning-label").htmlFor = courseSearch.id;

        const openCurrentCourses = () => openCourseList(Boolean(courseSelect.value));
        courseSearch.addEventListener("focus", openCurrentCourses);
        courseSearch.addEventListener("click", () => {
            if (courseList.hidden) {
                openCurrentCourses();
            }
        });
        courseSearch.addEventListener("input", () => {
            syncCourseValue();
            openCourseList();
        });
        courseSearch.addEventListener("keydown", (event) => {
            if (event.isComposing) {
                return;
            }
            if (event.key === "Escape") {
                event.preventDefault();
                closeCourseList();
            } else if (event.key === "Tab") {
                closeCourseList();
            } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                event.preventDefault();
                if (courseList.hidden) {
                    openCurrentCourses();
                }
                const count = visibleCourseOptions.length;
                if (count) {
                    const next = activeCourseIndex < 0
                        ? (event.key === "ArrowDown" ? 0 : count - 1)
                        : (activeCourseIndex + (event.key === "ArrowDown" ? 1 : -1) + count) % count;
                    setActiveCourse(next);
                }
            } else if (event.key === "Enter" && !courseList.hidden) {
                event.preventDefault();
                if (activeCourseIndex >= 0) {
                    chooseCourse(visibleCourseOptions[activeCourseIndex]);
                } else {
                    closeCourseList();
                }
            }
        });
        courseList.addEventListener("mousedown", (event) => {
            if (event.target.closest('[role="option"]')) {
                event.preventDefault();
            }
        });
        courseList.addEventListener("click", (event) => {
            const option = event.target.closest('[role="option"]');
            if (option && courseOptions.includes(option) && !option.hidden) {
                chooseCourse(option);
            }
        });
        for (const eventName of ["pointerdown", "focusin"]) {
            document.addEventListener(eventName, (event) => {
                if (!coursePicker.contains(event.target)) {
                    closeCourseList();
                }
            });
        }
    }

    const updateCourseMode = (clearInactive) => {
        const mode = selectedRadioValue("utbildningskalla");
        setConditionalFields(catalogCourseFields, mode === "katalog", clearInactive);
        setConditionalFields(customCourseFields, mode === "fritext", clearInactive);
        closeCourseList();
        if (courseSearch && courseSelect) {
            if (mode === "katalog") {
                syncCourseValue();
            } else {
                courseSearch.setCustomValidity("");
            }
        }
    };

    form.querySelectorAll('input[name="utbildningskalla"]').forEach((radio) => {
        radio.addEventListener("change", () => updateCourseMode(true));
    });

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
