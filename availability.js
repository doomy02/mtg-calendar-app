const picker = document.querySelector(".profile-grid");
const form = document.querySelector('form[action="/save-availability-week"]');
const message = document.querySelector("#picker-status");
const hours = [...picker.querySelectorAll(".pick-hour")];
const draft = new Map();
let anchor = null;
let selection = [];

function refresh() {
    for (const hour of hours) {
        const state = draft.get(hour.dataset.slot) ?? hour.dataset.state;
        for (const name of ["Available", "Maybe", "Unavailable"]) {
            hour.classList.toggle(name.toLowerCase(), state === name);
        }
        hour.classList.toggle("draft-hour", draft.has(hour.dataset.slot));
        hour.setAttribute("aria-pressed", String(selection.includes(hour)));
        hour.setAttribute("aria-label", `${hour.dataset.day} ${hour.textContent} · ${state || "Unset"}`);
    }
}

picker.addEventListener("click", (event) => {
    const hour = event.target.closest(".pick-hour");
    if (!hour) return;
    if (anchor?.dataset.day === hour.dataset.day) {
        const low = Math.min(Number(anchor.dataset.hour), Number(hour.dataset.hour));
        const high = Math.max(Number(anchor.dataset.hour), Number(hour.dataset.hour));
        selection = hours.filter(slot => slot.dataset.day === hour.dataset.day
            && Number(slot.dataset.hour) >= low && Number(slot.dataset.hour) <= high);
        anchor = null;
    } else {
        anchor = hour;
        selection = [hour];
    }
    message.textContent = `${selection.length} hours selected. Choose a status and tap Add block.`;
    refresh();
});

document.querySelector("#add-block").addEventListener("click", () => {
    if (!selection.length) {
        message.textContent = "Select hours first.";
        return;
    }
    for (const hour of selection) draft.set(hour.dataset.slot, form.elements.state.value);
    selection = [];
    anchor = null;
    message.textContent = `${draft.size} hours ready to save. Add another block or tap Save week.`;
    refresh();
});

document.querySelector("#discard-blocks").addEventListener("click", () => {
    draft.clear();
    selection = [];
    anchor = null;
    message.textContent = "Draft discarded.";
    refresh();
});

form.addEventListener("submit", (event) => {
    if (selection.length || !draft.size) {
        event.preventDefault();
        message.textContent = "Tap Add block before saving the week.";
        return;
    }
    form.elements.changes.value = JSON.stringify(Object.fromEntries(draft));
});
