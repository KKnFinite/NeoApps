(() => {
    "use strict";
    if (window.NeoStaffingPeople) return;
    const bindPeopleDrawerClose = () => {
        const console = document.querySelector(".neostaffing-people-console");
        if (!console) {
            return;
        }
        const closePeopleDrawers = () => {
            document.querySelectorAll("[data-neostaffing-drawer]").forEach((drawer) => {
                drawer.open = false;
            });
            document.querySelectorAll(".neostaffing-people-detail-drawer").forEach((drawer) => {
                drawer.hidden = true;
            });
        };
        document.addEventListener("click", (event) => {
            if (event.target.closest("[data-neostaffing-close-drawer]")) {
                closePeopleDrawers();
            }
        });
        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape") {
                closePeopleDrawers();
            }
        });
    };

    const bindPeopleEntryEnhancements = () => {
        const console = document.querySelector(".neostaffing-people-console");
        if (!console) return;
        const singleForm = document.querySelector('form[action="/neostaffing/app-management/people"]');
        const employeeId = singleForm?.querySelector('[name="employee_id"]');
        const seniority = singleForm?.querySelector('[name="seniority_date"]');
        const phone = singleForm?.querySelector('[name="phone_number"]');
        const classification = singleForm?.querySelector('[name="classification"]');
        document.querySelectorAll('[data-people-open-add="single"]').forEach((button) => {
            button.addEventListener("click", () => {
                requestAnimationFrame(() => employeeId?.focus());
            });
        });
        seniority?.addEventListener("change", () => {
            if (/^\d{4}-\d{2}-\d{2}$/.test(seniority.value)) phone?.focus();
        });
        phone?.addEventListener("input", () => {
            if (phone.value.replace(/\D/g, "").length === 10) classification?.focus();
        });
        singleForm?.addEventListener("submit", () => {
            try {
                sessionStorage.setItem("neostaffing.people.single-add", JSON.stringify(
                    Object.fromEntries(new FormData(singleForm).entries())
                ));
            } catch (_error) {}
        });
        const stored = (() => { try { return JSON.parse(sessionStorage.getItem("neostaffing.people.single-add")); } catch (_error) { return null; } })();
        if (stored && singleForm) {
            const succeeded = document.body.textContent.includes("Person added.");
            if (!succeeded) {
                Object.entries(stored).forEach(([name, value]) => {
                    const field = singleForm.elements.namedItem(name);
                    if (field && "value" in field) field.value = value;
                });
            }
            const drawer = document.querySelector('[data-people-add-drawer="single"]');
            if (drawer) drawer.open = true;
            requestAnimationFrame(() => employeeId?.focus());
            try { sessionStorage.removeItem("neostaffing.people.single-add"); } catch (_error) {}
        }
        const destination = document.querySelector('#people-selection-form select[name="work_area_unit_id"]');
        if (destination && !destination.dataset.peopleHierarchyPicker) {
            const root = Object.create(null);
            Array.from(destination.options).filter((option) => option.value).forEach((option) => {
                let branch = root;
                option.textContent.split(" / ").forEach((part, index, parts) => {
                    branch[part] ||= { children: Object.create(null), option: null };
                    if (index === parts.length - 1) branch[part].option = option;
                    branch = branch[part].children;
                });
            });
            const picker = document.createElement("details");
            picker.className = "neostaffing-people-destination-picker";
            const label = document.createElement("summary");
            label.dataset.peopleDestinationLabel = "";
            label.textContent = "Select Work Area";
            picker.append(label);
            const render = (items) => {
                const list = document.createElement("ul");
                Object.entries(items).forEach(([name, item]) => {
                    const row = document.createElement("li");
                    if (item.option) {
                        const button = document.createElement("button");
                        button.type = "button"; button.textContent = name;
                        button.addEventListener("click", () => {
                            destination.value = item.option.value;
                            picker.querySelector("[data-people-destination-label]").textContent = item.option.textContent;
                            picker.open = false;
                        });
                        row.append(button);
                    } else {
                        const group = document.createElement("details");
                        group.open = destination.selectedOptions[0]?.textContent.includes(name) || false;
                        const summary = document.createElement("summary");
                        summary.textContent = name;
                        group.append(summary);
                        group.append(render(item.children)); row.append(group);
                    }
                    list.append(row);
                });
                return list;
            };
            picker.append(render(root));
            destination.hidden = true;
            destination.after(picker);
            destination.dataset.peopleHierarchyPicker = "true";
        }
    };


    window.NeoStaffingPeople = Object.freeze({});
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => {bindPeopleDrawerClose(); bindPeopleEntryEnhancements();});
    else {bindPeopleDrawerClose(); bindPeopleEntryEnhancements();}
})();
