(function () {
  "use strict";

  var allowedEndpoints = new Set([
    "/api/gdp-vintages",
    "/api/table-inspector",
    "/api/agent-tools",
    "/api/agent-tools/call"
  ]);

  function localEndpoint(url) {
    var resolved = new URL(url, window.location.origin);
    if (resolved.origin !== window.location.origin || !allowedEndpoints.has(resolved.pathname)) {
      throw new Error("This local dashboard endpoint is not permitted.");
    }
    return resolved;
  }

  function outputFor(form) {
    var panel = form.closest(".panel");
    if (!panel) {
      return null;
    }
    return panel.querySelector("[data-dashboard-result], [data-dashboard-runner-result]");
  }

  function setOutput(output, state, message, value) {
    if (!output) {
      return;
    }
    output.dataset.state = state;
    output.textContent = message;
    if (value !== undefined) {
      output.textContent += "\n\n" + JSON.stringify(value, null, 2);
    }
  }

  async function readJson(endpoint, options) {
    var response = await fetch(endpoint.pathname + endpoint.search, options);
    var payload;
    try {
      payload = await response.json();
    } catch (_error) {
      throw new Error("The local endpoint did not return strict JSON.");
    }
    if (!response.ok) {
      var message = payload && payload.error && payload.error.message;
      throw new Error(typeof message === "string" ? message : "The local read request failed.");
    }
    return payload;
  }

  var MAX_ARGUMENT_CHARACTERS = 65536;
  var MAX_ARGUMENT_DEPTH = 32;

  function StrictJsonParser(source) {
    this.source = source;
    this.index = 0;
  }

  StrictJsonParser.prototype.fail = function (message) {
    throw new Error("Arguments must be one strict JSON object: " + message);
  };

  StrictJsonParser.prototype.peek = function () {
    return this.source.charAt(this.index);
  };

  StrictJsonParser.prototype.skipWhitespace = function () {
    while (this.index < this.source.length) {
      var character = this.peek();
      if (character !== " " && character !== "\n" && character !== "\r" && character !== "\t") {
        return;
      }
      this.index += 1;
    }
  };

  StrictJsonParser.prototype.expect = function (character) {
    if (this.peek() !== character) {
      this.fail("unexpected token at character " + (this.index + 1));
    }
    this.index += 1;
  };

  StrictJsonParser.prototype.parseString = function () {
    var start = this.index;
    this.expect('"');
    while (this.index < this.source.length) {
      var character = this.peek();
      var codeUnit = this.source.charCodeAt(this.index);
      if (character === '"') {
        this.index += 1;
        try {
          return JSON.parse(this.source.slice(start, this.index));
        } catch (_error) {
          this.fail("invalid string at character " + (start + 1));
        }
      }
      if (codeUnit <= 0x1f) {
        this.fail("unescaped control character at character " + (this.index + 1));
      }
      if (character === "\\") {
        this.index += 1;
        if (this.index >= this.source.length) {
          this.fail("unterminated string escape");
        }
        if (this.peek() === "u") {
          this.index += 5;
        } else {
          this.index += 1;
        }
        continue;
      }
      this.index += 1;
    }
    this.fail("unterminated string");
  };

  StrictJsonParser.prototype.parseNumber = function () {
    var start = this.index;
    if (this.peek() === "-") {
      this.index += 1;
    }
    if (this.peek() === "0") {
      this.index += 1;
      if (/^[0-9]$/.test(this.peek())) {
        this.fail("leading zero at character " + (this.index + 1));
      }
    } else if (/^[1-9]$/.test(this.peek())) {
      this.index += 1;
      while (/^[0-9]$/.test(this.peek())) {
        this.index += 1;
      }
    } else {
      this.fail("invalid number at character " + (this.index + 1));
    }
    if (this.peek() === ".") {
      this.index += 1;
      if (!/^[0-9]$/.test(this.peek())) {
        this.fail("fractional part requires a digit at character " + (this.index + 1));
      }
      while (/^[0-9]$/.test(this.peek())) {
        this.index += 1;
      }
    }
    if (this.peek() === "e" || this.peek() === "E") {
      this.index += 1;
      if (this.peek() === "+" || this.peek() === "-") {
        this.index += 1;
      }
      if (!/^[0-9]$/.test(this.peek())) {
        this.fail("exponent requires a digit at character " + (this.index + 1));
      }
      while (/^[0-9]$/.test(this.peek())) {
        this.index += 1;
      }
    }
    if (!Number.isFinite(Number(this.source.slice(start, this.index)))) {
      this.fail("numbers must be finite and within the browser-safe range");
    }
  };

  StrictJsonParser.prototype.parseLiteral = function (literal) {
    if (this.source.slice(this.index, this.index + literal.length) !== literal) {
      this.fail("invalid literal at character " + (this.index + 1));
    }
    this.index += literal.length;
  };

  StrictJsonParser.prototype.parseArray = function (depth) {
    this.expect("[");
    this.skipWhitespace();
    if (this.peek() === "]") {
      this.index += 1;
      return;
    }
    while (true) {
      this.parseValue(depth + 1);
      this.skipWhitespace();
      if (this.peek() === "]") {
        this.index += 1;
        return;
      }
      this.expect(",");
      this.skipWhitespace();
    }
  };

  StrictJsonParser.prototype.parseObject = function (depth) {
    this.expect("{");
    this.skipWhitespace();
    var keys = new Set();
    if (this.peek() === "}") {
      this.index += 1;
      return;
    }
    while (true) {
      if (this.peek() !== '"') {
        this.fail("object key must be a string at character " + (this.index + 1));
      }
      var key = this.parseString();
      if (keys.has(key)) {
        this.fail("duplicate object key");
      }
      keys.add(key);
      this.skipWhitespace();
      this.expect(":");
      this.skipWhitespace();
      this.parseValue(depth + 1);
      this.skipWhitespace();
      if (this.peek() === "}") {
        this.index += 1;
        return;
      }
      this.expect(",");
      this.skipWhitespace();
    }
  };

  StrictJsonParser.prototype.parseValue = function (depth) {
    if (depth > MAX_ARGUMENT_DEPTH) {
      this.fail("nesting exceeds the bounded depth");
    }
    this.skipWhitespace();
    var character = this.peek();
    if (character === "{") {
      this.parseObject(depth);
      return;
    }
    if (character === "[") {
      this.parseArray(depth);
      return;
    }
    if (character === '"') {
      this.parseString();
      return;
    }
    if (character === "t") {
      this.parseLiteral("true");
      return;
    }
    if (character === "f") {
      this.parseLiteral("false");
      return;
    }
    if (character === "n") {
      this.parseLiteral("null");
      return;
    }
    this.parseNumber();
  };

  function rejectUnpairedSourceSurrogates(source) {
    for (var index = 0; index < source.length; index += 1) {
      var codeUnit = source.charCodeAt(index);
      if (codeUnit >= 0xd800 && codeUnit <= 0xdbff) {
        var next = source.charCodeAt(index + 1);
        if (next < 0xdc00 || next > 0xdfff) {
          throw new Error("Arguments must be one strict JSON object: unpaired Unicode surrogate.");
        }
        index += 1;
      } else if (codeUnit >= 0xdc00 && codeUnit <= 0xdfff) {
        throw new Error("Arguments must be one strict JSON object: unpaired Unicode surrogate.");
      }
    }
  }

  function strictArgumentObject(source) {
    if (typeof source !== "string") {
      throw new Error("Arguments must be one strict JSON object.");
    }
    if (source.length === 0 || source.length > MAX_ARGUMENT_CHARACTERS) {
      throw new Error("Arguments must be one bounded strict JSON object.");
    }
    rejectUnpairedSourceSurrogates(source);
    var parser = new StrictJsonParser(source);
    parser.skipWhitespace();
    if (parser.peek() !== "{") {
      throw new Error("Arguments must be one strict JSON object.");
    }
    parser.parseObject(0);
    parser.skipWhitespace();
    if (parser.index !== source.length) {
      throw new Error("Arguments must be one strict JSON object: trailing content is not allowed.");
    }
    return source;
  }

  function createToolEnvelope(apiVersion, toolName, rawArguments) {
    var argumentsSource = strictArgumentObject(rawArguments);
    if (typeof apiVersion !== "string" || !apiVersion || typeof toolName !== "string" || !toolName) {
      throw new Error("A public API version and tool name are required.");
    }
    return "{\"api_version\":" + JSON.stringify(apiVersion)
      + ",\"tool\":" + JSON.stringify(toolName)
      + ",\"arguments\":" + argumentsSource + "}";
  }

  async function submitToolEnvelope(endpoint, envelope) {
    return readJson(endpoint, {
      method: "POST",
      headers: { "Accept": "application/json", "Content-Type": "application/json" },
      credentials: "same-origin",
      body: envelope
    });
  }

  async function runToolRequest(endpoint, apiVersion, toolName, rawArguments) {
    return submitToolEnvelope(endpoint, createToolEnvelope(apiVersion, toolName, rawArguments));
  }

  function enhanceReadForm(form) {
    var asOfMode = form.querySelector("[data-as-of-mode]");
    var asOfInput = form.querySelector("[data-as-of-input]");
    if (asOfMode && asOfInput) {
      var updateAsOf = function () {
        asOfInput.disabled = asOfMode.value !== "as_of";
        if (asOfInput.disabled) {
          asOfInput.value = "";
        }
      };
      asOfMode.addEventListener("change", updateAsOf);
      updateAsOf();
    }

    form.addEventListener("submit", async function (event) {
      event.preventDefault();
      var output = outputFor(form);
      try {
        var endpoint = localEndpoint(form.getAttribute("action") || "");
        var parameters = new URLSearchParams(new FormData(form));
        endpoint.search = parameters.toString();
        setOutput(output, "loading", "Loading bounded local read result…");
        var payload = await readJson(endpoint, {
          method: "GET",
          headers: { "Accept": "application/json" },
          credentials: "same-origin"
        });
        setOutput(output, "ready", "Bounded local read result returned.", payload);
        window.history.replaceState({}, "", window.location.pathname + "?" + parameters.toString());
      } catch (error) {
        setOutput(output, "error", error instanceof Error ? error.message : "The local read request failed.");
      }
    });
  }

  function enhanceRunner(form) {
    var toolSelect = form.querySelector("select[name='tool']");
    var argumentsInput = form.querySelector("textarea[name='arguments']");
    var loadExample = function () {
      if (!toolSelect || !argumentsInput || !toolSelect.selectedOptions.length) {
        return;
      }
      argumentsInput.value = toolSelect.selectedOptions[0].dataset.example || "{}";
    };
    if (toolSelect && argumentsInput) {
      toolSelect.addEventListener("change", loadExample);
      loadExample();
    }

    form.addEventListener("submit", async function (event) {
      event.preventDefault();
      var output = outputFor(form);
      try {
        var endpoint = localEndpoint(form.getAttribute("action") || "");
        var data = new FormData(form);
        var envelope = createToolEnvelope(
          String(data.get("api_version") || ""),
          String(data.get("tool") || ""),
          String(data.get("arguments") || "")
        );
        setOutput(output, "loading", "Running bounded read-only tool…");
        var payload = await submitToolEnvelope(endpoint, envelope);
        var status = payload && payload.result && payload.result.status;
        var state = typeof status === "string" ? status : "succeeded";
        setOutput(output, state, "Read-only tool result returned.", payload);
      } catch (error) {
        setOutput(output, "error", error instanceof Error ? error.message : "The tool request failed.");
      }
    });
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = Object.freeze({
      createToolEnvelope: createToolEnvelope,
      runToolRequest: runToolRequest,
      strictArgumentObject: strictArgumentObject
    });
  }

  if (typeof document !== "undefined") {
    document.querySelectorAll("[data-dashboard-form]").forEach(enhanceReadForm);
    document.querySelectorAll("[data-dashboard-runner]").forEach(enhanceRunner);
  }
}());
