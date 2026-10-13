/* The coverage pages' script, the workshop's own. On an index: sorting
   by a column, a filter by name, hiding the rows already covered, and
   totals of the rows shown. On a file's page: the highlight of each
   kind of line on or off, moving between highlighted chunks, and the
   contexts that reached a line. A reader's choices are kept in the
   browser's storage; a browser that refuses storage keeps them for the
   page's life alone. */
(function () {
  "use strict";

  const body = document.body;
  const PREFIX = "workshop-coverage:";

  function load(key, fallback) {
    try {
      const held = window.localStorage.getItem(PREFIX + key);
      return held === null ? fallback : JSON.parse(held);
    } catch (error) {
      return fallback;
    }
  }

  function save(key, value) {
    try {
      window.localStorage.setItem(PREFIX + key, JSON.stringify(value));
    } catch (error) {
      /* Storage refused: the choice lasts as long as the page. */
    }
  }

  /* Python's round, half to even, so a total reads as the server drew it. */
  function rounded(value, precision) {
    const factor = Math.pow(10, precision);
    const scaled = value * factor;
    const floor = Math.floor(scaled);
    if (Math.abs(scaled - floor - 0.5) < 1e-9) {
      return (floor % 2 === 0 ? floor : floor + 1) / factor;
    }
    return Math.round(scaled) / factor;
  }

  /* coverage.py's percentage: only nothing is 0 and only everything 100. */
  function percent(part, whole, precision) {
    let value = whole ? (100 * part) / whole : 100;
    const near = 1 / Math.pow(10, precision);
    if (value > 0 && value < near) {
      value = near;
    } else if (value > 100 - near && value < 100) {
      value = 100 - near;
    } else {
      value = rounded(value, precision);
    }
    return value.toFixed(precision) + "%";
  }

  function onKeys(actions) {
    document.addEventListener("keydown", function (event) {
      if (event.altKey || event.ctrlKey || event.metaKey) {
        return;
      }
      const target = event.target;
      if (
        target instanceof HTMLInputElement ||
        target instanceof HTMLTextAreaElement ||
        (target instanceof HTMLElement && target.isContentEditable)
      ) {
        return;
      }
      const action = actions[event.key];
      if (action) {
        event.preventDefault();
        action();
      }
    });
  }

  function helpPanel() {
    const button = document.querySelector(".help-toggle");
    const panel = document.getElementById("help");
    if (!button || !panel) {
      return function () {};
    }
    function flip() {
      panel.hidden = !panel.hidden;
      button.setAttribute("aria-expanded", String(!panel.hidden));
    }
    button.addEventListener("click", flip);
    return flip;
  }

  function go(address) {
    if (address) {
      window.location.href = address;
    }
  }

  function index() {
    const table = document.querySelector("table.index");
    if (!table) {
      return;
    }
    const noun = body.dataset.noun || "file";
    const precision = Number(body.dataset.precision || "0");
    const tbody = table.tBodies[0];
    const rows = Array.from(tbody.rows);
    const heads = Array.from(table.querySelectorAll("tr.columns th"));
    const filter = document.getElementById("filter");
    const hide = document.getElementById("hide-covered");
    const empty = document.querySelector(".no-rows");
    const keys = ["statements", "executed", "excluded", "branches", "taken",
      "partial", "covered", "coverable"];

    function sortBy(column, direction) {
      const position = heads.findIndex(function (th) {
        return th.dataset.column === column;
      });
      if (position < 0) {
        return;
      }
      const numeric = heads[position].dataset.type === "number";
      const ordered = rows.slice().sort(function (a, b) {
        const left = a.cells[position].dataset.sort || "";
        const right = b.cells[position].dataset.sort || "";
        const order = numeric
          ? Number(left) - Number(right)
          : left.localeCompare(right, undefined, { numeric: true });
        return direction === "ascending" ? order : -order;
      });
      ordered.forEach(function (row) {
        tbody.appendChild(row);
      });
      heads.forEach(function (th) {
        th.setAttribute("aria-sort", th.dataset.column === column ? direction : "none");
      });
      save("sort:" + noun, { column: column, direction: direction });
    }

    function next(th) {
      const now = th.getAttribute("aria-sort");
      if (now === "ascending") {
        return "descending";
      }
      if (now === "descending") {
        return "ascending";
      }
      return th.dataset.type === "number" ? "descending" : "ascending";
    }

    function setTotal(key, text) {
      const cell = table.querySelector('tfoot [data-total="' + key + '"]');
      if (cell) {
        cell.textContent = text;
      }
    }

    function apply() {
      const text = filter ? filter.value.toLowerCase() : "";
      const hideCovered = hide ? hide.checked : false;
      const sums = {};
      keys.forEach(function (key) {
        sums[key] = 0;
      });
      let visible = 0;
      rows.forEach(function (row) {
        const names = Array.from(row.querySelectorAll("td.name"))
          .map(function (cell) {
            return cell.textContent.toLowerCase();
          })
          .join(" ");
        const covered = row.dataset.covered === row.dataset.coverable;
        const show = names.indexOf(text) >= 0 && !(hideCovered && covered);
        row.hidden = !show;
        if (show) {
          visible += 1;
          keys.forEach(function (key) {
            sums[key] += Number(row.dataset[key] || "0");
          });
        }
      });
      if (empty) {
        empty.hidden = visible !== 0;
      }
      setTotal("statements", String(sums.statements));
      setTotal("missing", String(sums.statements - sums.executed));
      setTotal("excluded", String(sums.excluded));
      setTotal("branches", String(sums.branches));
      setTotal("partial", String(sums.partial));
      setTotal("statements-coverage", percent(sums.executed, sums.statements, precision));
      setTotal("branches-coverage", percent(sums.taken, sums.branches, precision));
      setTotal("coverage", percent(sums.covered, sums.coverable, precision));
      if (filter) {
        save("filter", filter.value);
      }
      if (hide) {
        save("hide-covered", hide.checked);
      }
    }

    const shortcuts = {};
    heads.forEach(function (th) {
      const button = th.querySelector("button");
      if (button) {
        button.addEventListener("click", function () {
          sortBy(th.dataset.column, next(th));
        });
      }
      if (th.dataset.key) {
        shortcuts[th.dataset.key] = function () {
          sortBy(th.dataset.column, next(th));
        };
      }
    });
    if (filter) {
      filter.value = load("filter", "");
      filter.addEventListener("input", apply);
    }
    if (hide) {
      hide.checked = Boolean(load("hide-covered", false));
      hide.addEventListener("change", apply);
    }
    const sorted = load("sort:" + noun, null);
    if (sorted && sorted.column) {
      sortBy(sorted.column, sorted.direction === "descending" ? "descending" : "ascending");
    }
    apply();
    shortcuts["["] = function () {
      go(body.dataset.prev);
    };
    shortcuts["]"] = function () {
      go(body.dataset.next);
    };
    shortcuts["?"] = helpPanel();
    onKeys(shortcuts);
  }

  function file() {
    const toggles = Array.from(document.querySelectorAll("button.toggle"));
    const lines = Array.from(document.querySelectorAll("main.source .ln"));
    const state = load("lines", { run: false, mis: true, exc: true, par: true });
    const categories = ["run", "mis", "exc", "par"];

    function show(category, on) {
      body.classList.toggle("hide-" + category, !on);
      toggles.forEach(function (button) {
        if (button.dataset.category === category) {
          button.setAttribute("aria-pressed", String(on));
        }
      });
    }

    function flip(category) {
      state[category] = body.classList.contains("hide-" + category);
      show(category, state[category]);
      save("lines", state);
    }

    categories.forEach(function (category) {
      show(category, state[category] !== false);
    });
    toggles.forEach(function (button) {
      button.addEventListener("click", function () {
        flip(button.dataset.category);
      });
    });

    function highlighted(line) {
      return categories.some(function (category) {
        const drawn = line.classList.contains(category) ||
          line.classList.contains(category + "2");
        return drawn && !body.classList.contains("hide-" + category);
      });
    }

    function chunks() {
      const found = [];
      let current = null;
      lines.forEach(function (line, position) {
        if (!highlighted(line)) {
          current = null;
          return;
        }
        if (current) {
          current.end = position;
        } else {
          current = { start: position, end: position };
          found.push(current);
        }
      });
      return found;
    }

    let selected = -1;

    function select(position) {
      const all = chunks();
      lines.forEach(function (line) {
        line.classList.remove("selected");
      });
      if (!all.length) {
        selected = -1;
        return;
      }
      selected = Math.max(0, Math.min(position, all.length - 1));
      const chunk = all[selected];
      for (let at = chunk.start; at <= chunk.end; at += 1) {
        lines[at].classList.add("selected");
      }
      lines[chunk.start].scrollIntoView({ block: "center" });
    }

    const names = (function () {
      const holder = document.getElementById("contexts");
      if (!holder) {
        return [];
      }
      try {
        return JSON.parse(holder.textContent);
      } catch (error) {
        return [];
      }
    })();

    document.querySelectorAll("button.ctx").forEach(function (button) {
      button.addEventListener("click", function () {
        const line = button.closest(".ln");
        const open = line.querySelector("ol.ctx-list");
        if (open) {
          open.remove();
          button.setAttribute("aria-expanded", "false");
          return;
        }
        const list = document.createElement("ol");
        list.className = "ctx-list";
        (button.dataset.contexts || "").split(" ").forEach(function (place) {
          if (place === "") {
            return;
          }
          const item = document.createElement("li");
          item.textContent = names[Number(place)];
          list.appendChild(item);
        });
        line.appendChild(list);
        button.setAttribute("aria-expanded", "true");
      });
    });

    function link(relation) {
      const anchor = document.querySelector('.pager a[rel="' + relation + '"]');
      return anchor ? anchor.getAttribute("href") : "";
    }

    onKeys({
      r: function () {
        flip("run");
      },
      m: function () {
        flip("mis");
      },
      x: function () {
        flip("exc");
      },
      p: function () {
        flip("par");
      },
      j: function () {
        select(selected + 1);
      },
      k: function () {
        select(selected - 1);
      },
      0: function () {
        selected = -1;
        window.scrollTo(0, 0);
      },
      1: function () {
        select(0);
      },
      "[": function () {
        go(link("prev"));
      },
      "]": function () {
        go(link("next"));
      },
      u: function () {
        go("index.html");
      },
      "?": helpPanel(),
    });
  }

  if (body.classList.contains("index-page")) {
    index();
  } else if (body.classList.contains("file-page")) {
    file();
  }
})();
