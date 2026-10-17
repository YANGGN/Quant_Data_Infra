(function () {
  "use strict";

  var MAX_ARGUMENT_CHARACTERS = 8388608;
  var MAX_ARGUMENT_DEPTH = 32;
  var MAX_DECIMAL_DIGITS = 4096;
  var MAX_DECIMAL_EXPONENT = 4096n;
  var MAX_TREE_PREVIEW_ENTRIES = 500;
  var MAX_RAW_PREVIEW_CHARACTERS = 131072;
  var MANIFEST_ENDPOINT = "/api/agent-tools";
  var CALL_ENDPOINT = "/api/agent-tools/call";

  function StrictJsonNumber(lexeme) {
    this.lexeme = lexeme;
    Object.freeze(this);
  }

  StrictJsonNumber.prototype.toString = function () {
    return this.lexeme;
  };

  function isStrictJsonNumber(value) {
    return value instanceof StrictJsonNumber;
  }

  function numericLexeme(value) {
    if (isStrictJsonNumber(value)) {
      return value.lexeme;
    }
    if (typeof value === "number" && Number.isFinite(value)) {
      return String(value);
    }
    return null;
  }

  function jsonValuesEqual(left, right) {
    if (isStrictJsonNumber(left) && isStrictJsonNumber(right)) {
      return left.lexeme === right.lexeme;
    }
    return left === right;
  }

  function stringifyStrictJson(value, space) {
    var width = typeof space === "number"
      ? Math.max(0, Math.min(10, Math.floor(space)))
      : 0;
    var gap = " ".repeat(width);
    var active = new Set();

    function encode(item, depth) {
      if (depth > MAX_ARGUMENT_DEPTH) {
        throw new Error("Strict JSON nesting exceeds " + MAX_ARGUMENT_DEPTH + " levels.");
      }
      if (isStrictJsonNumber(item)) {
        return item.lexeme;
      }
      if (item === null) {
        return "null";
      }
      if (typeof item === "string") {
        return JSON.stringify(item);
      }
      if (typeof item === "boolean") {
        return item ? "true" : "false";
      }
      if (typeof item === "number") {
        if (!Number.isFinite(item) || !Number.isSafeInteger(item)) {
          throw new Error(
            "Native JavaScript numbers must be finite safe integers; "
            + "parse decimal values from strict JSON to preserve their exact lexemes."
          );
        }
        return String(item);
      }
      if (!item || typeof item !== "object") {
        throw new Error("Strict JSON contains an unsupported value.");
      }
      if (active.has(item)) {
        throw new Error("Strict JSON cannot contain a circular reference.");
      }
      active.add(item);
      var indentation = gap.repeat(depth);
      var childIndentation = gap.repeat(depth + 1);
      var result;
      if (Array.isArray(item)) {
        var arrayParts = item.map(function (child) {
          return encode(child, depth + 1);
        });
        result = !arrayParts.length
          ? "[]"
          : (gap
            ? "[\n" + childIndentation
              + arrayParts.join(",\n" + childIndentation)
              + "\n" + indentation + "]"
            : "[" + arrayParts.join(",") + "]");
      } else {
        var objectParts = [];
        Object.keys(item).forEach(function (key) {
          if (item[key] !== undefined) {
            objectParts.push(
              JSON.stringify(key)
              + (gap ? ": " : ":")
              + encode(item[key], depth + 1)
            );
          }
        });
        result = !objectParts.length
          ? "{}"
          : (gap
            ? "{\n" + childIndentation
              + objectParts.join(",\n" + childIndentation)
              + "\n" + indentation + "}"
            : "{" + objectParts.join(",") + "}");
      }
      active.delete(item);
      return result;
    }

    return encode(value, 0);
  }

  function StrictJsonParser(source) {
    this.source = source;
    this.index = 0;
  }

  StrictJsonParser.prototype.fail = function (message) {
    throw new Error("Strict JSON is required: " + message);
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
          var value = JSON.parse(this.source.slice(start, this.index));
          for (var valueIndex = 0; valueIndex < value.length; valueIndex += 1) {
            var valueCodeUnit = value.charCodeAt(valueIndex);
            if (valueCodeUnit >= 0xd800 && valueCodeUnit <= 0xdbff) {
              var nextCodeUnit = value.charCodeAt(valueIndex + 1);
              if (!(nextCodeUnit >= 0xdc00 && nextCodeUnit <= 0xdfff)) {
                this.fail("unpaired Unicode surrogate at character " + (start + 1));
              }
              valueIndex += 1;
            } else if (valueCodeUnit >= 0xdc00 && valueCodeUnit <= 0xdfff) {
              this.fail("unpaired Unicode surrogate at character " + (start + 1));
            }
          }
          return value;
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
          var escapeValue = this.source.slice(this.index + 1, this.index + 5);
          if (!/^[0-9a-fA-F]{4}$/.test(escapeValue)) {
            this.fail("invalid Unicode escape at character " + (this.index + 1));
          }
          this.index += 5;
        } else {
          if (!/^[\"\\/bfnrt]$/.test(this.peek())) {
            this.fail("invalid escape at character " + (this.index + 1));
          }
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
        this.fail("fractional part requires a digit");
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
        this.fail("exponent requires a digit");
      }
      while (/^[0-9]$/.test(this.peek())) {
        this.index += 1;
      }
    }
    var lexeme = this.source.slice(start, this.index);
    var unsigned = lexeme.charAt(0) === "-" ? lexeme.slice(1) : lexeme;
    if (!/[.eE]/.test(unsigned)) {
      if (unsigned.length > MAX_DECIMAL_DIGITS) {
        this.fail("integer exceeds the supported magnitude");
      }
      return new StrictJsonNumber(lexeme);
    }
    var exponentParts = unsigned.toLowerCase().split("e");
    var mantissa = exponentParts[0];
    var explicitExponent;
    try {
      var exponentToken = exponentParts.length === 2
        ? exponentParts[1]
        : "0";
      var exponentSign = "";
      if (exponentToken.charAt(0) === "+" || exponentToken.charAt(0) === "-") {
        exponentSign = exponentToken.charAt(0) === "-" ? "-" : "";
        exponentToken = exponentToken.slice(1);
      }
      var normalizedExponent = exponentToken.replace(/^0+/, "") || "0";
      if (normalizedExponent.length > 10) {
        this.fail("decimal exponent exceeds the supported magnitude");
      }
      explicitExponent = BigInt(exponentSign + normalizedExponent);
    } catch (_error) {
      this.fail("decimal exponent is invalid");
    }
    var decimalPoint = mantissa.indexOf(".");
    var fractionalDigits = decimalPoint === -1
      ? 0
      : mantissa.length - decimalPoint - 1;
    var coefficient = mantissa.replace(".", "");
    var significantDigits = coefficient.replace(/^0+/, "") || "0";
    var decimalExponent = explicitExponent - BigInt(fractionalDigits);
    var adjustedExponent = decimalExponent
      + BigInt(significantDigits.length)
      - 1n;
    if (
      significantDigits.length > MAX_DECIMAL_DIGITS
      || decimalExponent > MAX_DECIMAL_EXPONENT
      || decimalExponent < -MAX_DECIMAL_EXPONENT
      || adjustedExponent > MAX_DECIMAL_EXPONENT
      || adjustedExponent < -MAX_DECIMAL_EXPONENT
    ) {
      this.fail("decimal exceeds the supported magnitude");
    }
    return new StrictJsonNumber(lexeme);
  };

  StrictJsonParser.prototype.parseArray = function (depth) {
    this.expect("[");
    this.skipWhitespace();
    var values = [];
    if (this.peek() === "]") {
      this.index += 1;
      return values;
    }
    while (true) {
      values.push(this.parseValue(depth + 1));
      this.skipWhitespace();
      if (this.peek() === "]") {
        this.index += 1;
        return values;
      }
      this.expect(",");
      this.skipWhitespace();
    }
  };

  StrictJsonParser.prototype.parseObject = function (depth) {
    this.expect("{");
    this.skipWhitespace();
    var value = Object.create(null);
    var keys = new Set();
    if (this.peek() === "}") {
      this.index += 1;
      return value;
    }
    while (true) {
      if (this.peek() !== '"') {
        this.fail("object key must be a string");
      }
      var key = this.parseString();
      if (keys.has(key)) {
        this.fail("duplicate object key " + JSON.stringify(key));
      }
      keys.add(key);
      this.skipWhitespace();
      this.expect(":");
      this.skipWhitespace();
      value[key] = this.parseValue(depth + 1);
      this.skipWhitespace();
      if (this.peek() === "}") {
        this.index += 1;
        return value;
      }
      this.expect(",");
      this.skipWhitespace();
    }
  };

  StrictJsonParser.prototype.parseValue = function (depth) {
    if (depth > MAX_ARGUMENT_DEPTH) {
      this.fail("nesting exceeds " + MAX_ARGUMENT_DEPTH + " levels");
    }
    this.skipWhitespace();
    var character = this.peek();
    if (character === "{") {
      return this.parseObject(depth);
    }
    if (character === "[") {
      return this.parseArray(depth);
    }
    if (character === '"') {
      return this.parseString();
    }
    if (this.source.slice(this.index, this.index + 4) === "true") {
      this.index += 4;
      return true;
    }
    if (this.source.slice(this.index, this.index + 5) === "false") {
      this.index += 5;
      return false;
    }
    if (this.source.slice(this.index, this.index + 4) === "null") {
      this.index += 4;
      return null;
    }
    return this.parseNumber();
  };

  function parseStrictJson(source) {
    if (typeof source !== "string" || source.length === 0 || source.length > MAX_ARGUMENT_CHARACTERS) {
      throw new Error("Strict JSON must be non-empty and within the request bound.");
    }
    var parser = new StrictJsonParser(source);
    parser.skipWhitespace();
    var value = parser.parseValue(0);
    parser.skipWhitespace();
    if (parser.index !== source.length) {
      parser.fail("trailing content is not allowed");
    }
    return value;
  }

  function clone(value) {
    return parseStrictJson(stringifyStrictJson(value));
  }

  function localEndpoint(path) {
    var endpoint = new URL(path, window.location.origin);
    if (
      endpoint.origin !== window.location.origin
      || (endpoint.pathname !== MANIFEST_ENDPOINT && endpoint.pathname !== CALL_ENDPOINT)
      || endpoint.search
      || endpoint.hash
    ) {
      throw new Error("This local Inspector endpoint is not permitted.");
    }
    return endpoint.pathname;
  }

  function schemaTypes(schema) {
    var raw = schema && schema.type;
    if (Array.isArray(raw)) {
      return raw.slice();
    }
    return typeof raw === "string" ? [raw] : [];
  }

  function primaryType(schema) {
    var types = schemaTypes(schema).filter(function (item) {
      return item !== "null";
    });
    return types[0] || (schema && Object.prototype.hasOwnProperty.call(schema, "const")
      ? (isStrictJsonNumber(schema.const) ? "number" : typeof schema.const)
      : "json");
  }

  function allowsNull(schema) {
    return schemaTypes(schema).indexOf("null") !== -1;
  }

  function variants(tool) {
    return Array.isArray(tool && tool.versions) && tool.versions.length
      ? tool.versions
      : [tool];
  }

  function semanticVersionParts(value) {
    var match = /^(\d+)\.(\d+)\.(\d+)$/.exec(String(value || ""));
    if (!match) {
      return [-1, -1, -1];
    }
    return [Number(match[1]), Number(match[2]), Number(match[3])];
  }

  function latestVariant(tool) {
    var available = variants(tool);
    return available.reduce(function (latest, candidate) {
      if (!latest) {
        return candidate;
      }
      var left = semanticVersionParts(latest && latest.version);
      var right = semanticVersionParts(candidate && candidate.version);
      for (var index = 0; index < left.length; index += 1) {
        if (right[index] > left[index]) {
          return candidate;
        }
        if (right[index] < left[index]) {
          return latest;
        }
      }
      return latest;
    }, null);
  }

  function firstExample(variant) {
    return variant && Array.isArray(variant.examples) && variant.examples[0]
      && typeof variant.examples[0] === "object"
      ? clone(variant.examples[0])
      : {};
  }

  function containsTimeSeries(schema) {
    if (!schema || typeof schema !== "object") {
      return false;
    }
    if (
      schema.properties
      && schema.properties.contract
      && schema.properties.contract.const === "quant_data.timeseries"
    ) {
      return true;
    }
    return containsTimeSeries(schema.items);
  }

  function fieldHint(schema, required) {
    var pieces = [required ? "required" : "optional"];
    var minimum = numericLexeme(schema.minimum);
    var maximum = numericLexeme(schema.maximum);
    var minLength = numericLexeme(schema.minLength);
    var maxLength = numericLexeme(schema.maxLength);
    var minItems = numericLexeme(schema.minItems);
    var maxItems = numericLexeme(schema.maxItems);
    if (minimum !== null) {
      pieces.push("min " + minimum);
    }
    if (maximum !== null) {
      pieces.push("max " + maximum);
    }
    if (minLength !== null) {
      pieces.push("min length " + minLength);
    }
    if (maxLength !== null) {
      pieces.push("max length " + maxLength);
    }
    if (minItems !== null) {
      pieces.push("min items " + minItems);
    }
    if (maxItems !== null) {
      pieces.push("max items " + maxItems);
    }
    return pieces.join(" · ");
  }

  function option(value, label, selected) {
    var item = document.createElement("option");
    item.value = value;
    item.textContent = label;
    item.selected = selected;
    return item;
  }

  function nullableToggle(field, isNull) {
    var select = document.createElement("select");
    select.dataset.nullToggle = field;
    select.setAttribute("aria-label", field + " value state");
    select.appendChild(option("value", "Use value", !isNull));
    select.appendChild(option("null", "Use null", isNull));
    return select;
  }

  function complexInput(field, schema, value) {
    var textarea = document.createElement("textarea");
    textarea.name = "schema_" + field;
    textarea.rows = primaryType(schema) === "array" ? 7 : 5;
    textarea.dataset.schemaField = field;
    textarea.dataset.valueKind = "json";
    textarea.value = stringifyStrictJson(
      value === undefined
        ? (primaryType(schema) === "array" ? [] : {})
        : value,
      2
    );
    return textarea;
  }

  function scalarInput(field, schema, value) {
    var type = primaryType(schema);
    if (Array.isArray(schema.enum)) {
      var select = document.createElement("select");
      select.name = "schema_" + field;
      select.dataset.schemaField = field;
      select.dataset.valueKind = "enum";
      if (allowsNull(schema)) {
        select.appendChild(option("__NULL__", "Null", value === null));
      }
      schema.enum.forEach(function (item) {
        var serialized = stringifyStrictJson(item);
        select.appendChild(option(serialized, String(item), jsonValuesEqual(value, item)));
      });
      return select;
    }
    if (type === "boolean") {
      var booleanSelect = document.createElement("select");
      booleanSelect.name = "schema_" + field;
      booleanSelect.dataset.schemaField = field;
      booleanSelect.dataset.valueKind = "boolean";
      if (allowsNull(schema)) {
        booleanSelect.appendChild(option("null", "Null", value === null));
      }
      booleanSelect.appendChild(option("true", "True", value === true));
      booleanSelect.appendChild(option("false", "False", value === false));
      return booleanSelect;
    }
    var input = document.createElement("input");
    input.name = "schema_" + field;
    input.dataset.schemaField = field;
    input.dataset.valueKind = type;
    if (type === "number" || type === "integer") {
      input.type = "number";
      input.step = type === "integer" ? "1" : "any";
      var inputMinimum = numericLexeme(schema.minimum);
      var inputMaximum = numericLexeme(schema.maximum);
      if (inputMinimum !== null) {
        input.min = inputMinimum;
      }
      if (inputMaximum !== null) {
        input.max = inputMaximum;
      }
    } else {
      input.type = schema.format === "date" ? "date" : "text";
      var inputMaxLength = numericLexeme(schema.maxLength);
      if (inputMaxLength !== null) {
        input.setAttribute("maxlength", inputMaxLength);
      }
    }
    if (Object.prototype.hasOwnProperty.call(schema, "const")) {
      value = schema.const;
      input.readOnly = true;
    }
    input.value = value === null || value === undefined
      ? ""
      : (numericLexeme(value) !== null ? numericLexeme(value) : String(value));
    return input;
  }

  function seriesPicker(field, schema, state, textarea) {
    if (!containsTimeSeries(schema)) {
      return null;
    }
    var wrapper = document.createElement("div");
    wrapper.className = "series-picker";
    var select = document.createElement("select");
    select.dataset.seriesPicker = field;
    select.multiple = primaryType(schema) === "array";
    select.setAttribute("aria-label", "Reusable series for " + field);
    var button = document.createElement("button");
    button.type = "button";
    button.textContent = "Insert saved series";
    button.addEventListener("click", function () {
      var selected = Array.prototype.filter.call(select.options, function (item) {
        return item.selected;
      }).map(function (item) {
        return state.savedSeries[Number(item.value)].value;
      });
      if (!selected.length) {
        return;
      }
      textarea.value = stringifyStrictJson(
        primaryType(schema) === "array" ? selected : selected[0],
        2
      );
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
    });
    wrapper.appendChild(select);
    wrapper.appendChild(button);
    return wrapper;
  }

  function renderFields(container, variant, state) {
    container.replaceChildren();
    var schema = variant && variant.input_schema;
    var properties = schema && schema.properties;
    if (!properties || typeof properties !== "object") {
      var unavailable = document.createElement("p");
      unavailable.className = "empty-copy";
      unavailable.textContent = "This manifest variant has no object input properties.";
      container.appendChild(unavailable);
      return;
    }
    var example = firstExample(variant);
    var required = new Set(Array.isArray(schema.required) ? schema.required : []);
    Object.keys(properties).forEach(function (field) {
      var fieldSchema = properties[field] || {};
      var value = Object.prototype.hasOwnProperty.call(example, field)
        ? example[field]
        : (allowsNull(fieldSchema) ? null : undefined);
      var wrapper = document.createElement("div");
      wrapper.className = "schema-field";
      var label = document.createElement("label");
      var title = document.createElement("span");
      title.className = "schema-field-title";
      title.textContent = field.replaceAll("_", " ");
      var hint = document.createElement("span");
      hint.className = "field-note";
      hint.textContent = fieldHint(fieldSchema, required.has(field));
      label.appendChild(title);
      label.appendChild(hint);

      var type = primaryType(fieldSchema);
      var control;
      if (type === "array" || type === "object" || type === "json") {
        control = complexInput(field, fieldSchema, value);
      } else {
        control = scalarInput(field, fieldSchema, value);
      }
      control.dataset.required = required.has(field) ? "true" : "false";
      control.dataset.nullable = allowsNull(fieldSchema) ? "true" : "false";
      label.appendChild(control);
      if (
        allowsNull(fieldSchema)
        && type !== "boolean"
        && !Array.isArray(fieldSchema.enum)
      ) {
        var toggle = nullableToggle(field, value === null);
        label.appendChild(toggle);
        control.disabled = value === null;
        toggle.addEventListener("change", function () {
          control.disabled = toggle.value === "null";
          state.rawDirty = false;
          synchronizeArguments(state);
        });
      }
      var picker = seriesPicker(field, fieldSchema, state, control);
      if (picker) {
        label.appendChild(picker);
      }
      wrapper.appendChild(label);
      container.appendChild(wrapper);
    });
    refreshSeriesPickers(state);
  }

  function readControl(control) {
    var nullToggle = control.parentElement.querySelector(
      '[data-null-toggle="' + control.dataset.schemaField + '"]'
    );
    if (nullToggle && nullToggle.value === "null") {
      return null;
    }
    if (control.dataset.valueKind === "json") {
      return parseStrictJson(control.value);
    }
    if (control.dataset.valueKind === "enum") {
      return control.value === "__NULL__" ? null : parseStrictJson(control.value);
    }
    if (control.dataset.valueKind === "boolean") {
      return control.value === "null" ? null : control.value === "true";
    }
    if (control.dataset.valueKind === "integer") {
      if (control.value === "") {
        return control.dataset.nullable === "true" ? null : undefined;
      }
      if (!/^-?(?:0|[1-9][0-9]*)$/.test(control.value)) {
        throw new Error(control.dataset.schemaField + " must be an integer.");
      }
      return parseStrictJson(control.value);
    }
    if (control.dataset.valueKind === "number") {
      if (control.value === "") {
        return control.dataset.nullable === "true" ? null : undefined;
      }
      var number = parseStrictJson(control.value);
      if (!isStrictJsonNumber(number)) {
        throw new Error(control.dataset.schemaField + " must be a number.");
      }
      return number;
    }
    if (control.value === "" && control.dataset.nullable === "true") {
      return null;
    }
    if (control.value === "" && control.dataset.required === "false") {
      return undefined;
    }
    return control.value;
  }

  function structuredArguments(state) {
    var result = Object.create(null);
    state.fields.querySelectorAll("[data-schema-field]").forEach(function (control) {
      var value = readControl(control);
      if (value !== undefined) {
        result[control.dataset.schemaField] = value;
      }
    });
    return result;
  }

  function synchronizeArguments(state) {
    try {
      state.argumentsInput.value = stringifyStrictJson(structuredArguments(state), 2);
      state.rawDirty = false;
      state.argumentsInput.setCustomValidity("");
    } catch (error) {
      state.argumentsInput.setCustomValidity(
        error instanceof Error ? error.message : "A generated field is invalid."
      );
    }
  }

  function seriesKey(series) {
    return [
      series.series_id || "",
      series.lineage_digest || "",
      series.metadata && series.metadata.observation_field || "",
      series.metadata && series.metadata.component || ""
    ].join("|");
  }

  function collectSeries(value, output, seen, depth) {
    if (
      depth > MAX_ARGUMENT_DEPTH
      || value === null
      || typeof value !== "object"
      || isStrictJsonNumber(value)
    ) {
      return;
    }
    if (value.contract === "quant_data.timeseries") {
      var key = seriesKey(value);
      if (!seen.has(key)) {
        seen.add(key);
        output.push({
          key: key,
          label: String(
            value.series_id
            || value.metadata && value.metadata.component
            || "Returned time series"
          ),
          value: clone(value)
        });
      }
      return;
    }
    if (Array.isArray(value)) {
      value.forEach(function (item) {
        collectSeries(item, output, seen, depth + 1);
      });
      return;
    }
    Object.keys(value).forEach(function (key) {
      collectSeries(value[key], output, seen, depth + 1);
    });
  }

  function refreshSeriesPickers(state) {
    state.fields.querySelectorAll("[data-series-picker]").forEach(function (picker) {
      var selectedKeys = new Set(
        Array.prototype.filter.call(picker.options, function (item) {
          return item.selected;
        }).map(function (item) {
          return item.dataset.seriesKey;
        })
      );
      picker.replaceChildren();
      state.savedSeries.forEach(function (series, index) {
        var item = option(String(index), series.label, selectedKeys.has(series.key));
        item.dataset.seriesKey = series.key;
        picker.appendChild(item);
      });
      if (
        state.savedSeries.length
        && !Array.prototype.some.call(picker.options, function (item) {
          return item.selected;
        })
      ) {
        picker.options[0].selected = true;
      }
      picker.disabled = state.savedSeries.length === 0;
      var button = picker.parentElement.querySelector("button");
      if (button) {
        button.disabled = state.savedSeries.length === 0;
      }
    });
  }

  function renderSavedSeries(state) {
    state.savedSeriesPanel.replaceChildren();
    var heading = document.createElement("p");
    var strong = document.createElement("strong");
    strong.textContent = "Reusable series: ";
    heading.appendChild(strong);
    heading.appendChild(document.createTextNode(
      state.savedSeries.length
        ? state.savedSeries.length + " available in this browser session."
        : "none returned in this browser session."
    ));
    state.savedSeriesPanel.appendChild(heading);
    if (state.savedSeries.length) {
      var list = document.createElement("ul");
      state.savedSeries.forEach(function (series) {
        var item = document.createElement("li");
        item.textContent = series.label;
        list.appendChild(item);
      });
      state.savedSeriesPanel.appendChild(list);
    }
    refreshSeriesPickers(state);
  }

  function lazyDetails(summaryText, builder, open) {
    var details = document.createElement("details");
    var summary = document.createElement("summary");
    summary.textContent = summaryText;
    var content = document.createElement("div");
    content.className = "result-tree-children";
    content.dataset.unrendered = "true";
    details.appendChild(summary);
    details.appendChild(content);
    var render = function () {
      if (content.dataset.unrendered === "true") {
        delete content.dataset.unrendered;
        builder(content);
      }
    };
    details.addEventListener("toggle", function () {
      if (details.open) {
        render();
      }
    });
    if (open) {
      details.open = true;
      render();
    }
    return details;
  }

  function primitiveNode(value) {
    var span = document.createElement("span");
    span.className = "result-primitive result-" + (
      value === null ? "null" : typeof value
    );
    span.textContent = value === null
      ? "null"
      : (isStrictJsonNumber(value)
        ? value.lexeme
        : (typeof value === "string" ? value : JSON.stringify(value)));
    return span;
  }

  function previewNotice(shown, total) {
    var notice = document.createElement("p");
    notice.className = "result-preview-note";
    notice.textContent = "Rendered tree preview shows "
      + shown + " of " + total + " entries here. "
      + (total - shown) + " omitted from the tree; "
      + "the paged raw strict JSON below remains complete.";
    return notice;
  }

  function resultNode(value, depth, budget) {
    if (value === null || typeof value !== "object" || isStrictJsonNumber(value)) {
      return primitiveNode(value);
    }
    if (depth > MAX_ARGUMENT_DEPTH) {
      return primitiveNode("[depth bound reached]");
    }
    if (Array.isArray(value)) {
      return lazyDetails(
        "Array · " + value.length + (value.length === 1 ? " item" : " items"),
        function (container) {
          var list = document.createElement("ol");
          var shown = Math.min(value.length, budget.remaining);
          budget.remaining -= shown;
          value.slice(0, shown).forEach(function (item) {
            var row = document.createElement("li");
            row.appendChild(resultNode(item, depth + 1, budget));
            list.appendChild(row);
          });
          container.appendChild(list);
          if (shown < value.length) {
            container.appendChild(previewNotice(shown, value.length));
          }
        },
        depth < 1
      );
    }
    var keys = Object.keys(value);
    return lazyDetails(
      "Object · " + keys.length + (keys.length === 1 ? " field" : " fields"),
      function (container) {
        var list = document.createElement("dl");
        var shown = Math.min(keys.length, budget.remaining);
        budget.remaining -= shown;
        keys.slice(0, shown).forEach(function (key) {
          var term = document.createElement("dt");
          term.textContent = key;
          var definition = document.createElement("dd");
          definition.appendChild(resultNode(value[key], depth + 1, budget));
          list.appendChild(term);
          list.appendChild(definition);
        });
        container.appendChild(list);
        if (shown < keys.length) {
          container.appendChild(previewNotice(shown, keys.length));
        }
      },
      depth < 1
    );
  }

  function renderResult(output, payload, status) {
    output.replaceChildren();
    output.dataset.state = status >= 200 && status < 300 ? "succeeded" : "error";
    var heading = document.createElement("strong");
    heading.textContent = status >= 200 && status < 300
      ? "Read-only tool result returned."
      : "The read-only tool request was rejected.";
    output.appendChild(heading);
    var tree = document.createElement("section");
    tree.className = "result-tree";
    tree.setAttribute("aria-label", "Bounded nested tool result preview");
    tree.appendChild(resultNode(
      payload,
      0,
      { remaining: MAX_TREE_PREVIEW_ENTRIES }
    ));
    output.appendChild(tree);
    var rawSource = stringifyStrictJson(payload);
    var raw = lazyDetails(
      "Raw strict JSON · complete paged response",
      function (container) {
        var controls = document.createElement("div");
        controls.className = "raw-result-controls";
        var previous = document.createElement("button");
        previous.type = "button";
        previous.textContent = "Previous";
        var pageStatus = document.createElement("span");
        pageStatus.setAttribute("aria-live", "polite");
        var next = document.createElement("button");
        next.type = "button";
        next.textContent = "Next";
        var pre = document.createElement("pre");
        var page = 0;
        var pages = Math.max(
          1,
          Math.ceil(rawSource.length / MAX_RAW_PREVIEW_CHARACTERS)
        );
        var renderPage = function () {
          var nominalStart = page * MAX_RAW_PREVIEW_CHARACTERS;
          var start = nominalStart;
          var end = Math.min(
            rawSource.length,
            nominalStart + MAX_RAW_PREVIEW_CHARACTERS
          );
          if (
            start > 0
            && rawSource.charCodeAt(start) >= 0xdc00
            && rawSource.charCodeAt(start) <= 0xdfff
          ) {
            start -= 1;
          }
          if (
            end < rawSource.length
            && rawSource.charCodeAt(end - 1) >= 0xd800
            && rawSource.charCodeAt(end - 1) <= 0xdbff
          ) {
            end += 1;
          }
          pre.textContent = rawSource.slice(start, end);
          pageStatus.textContent = "Characters "
            + (start + 1) + "–" + end + " of " + rawSource.length
            + " · page " + (page + 1) + " of " + pages;
          previous.disabled = page === 0;
          next.disabled = page >= pages - 1;
        };
        previous.addEventListener("click", function () {
          if (page > 0) {
            page -= 1;
            renderPage();
          }
        });
        next.addEventListener("click", function () {
          if (page < pages - 1) {
            page += 1;
            renderPage();
          }
        });
        controls.appendChild(previous);
        controls.appendChild(pageStatus);
        controls.appendChild(next);
        container.appendChild(controls);
        container.appendChild(pre);
        renderPage();
      },
      false
    );
    raw.className = "raw-result";
    output.appendChild(raw);
  }

  function setMessage(output, state, message) {
    output.replaceChildren();
    output.dataset.state = state;
    var strong = document.createElement("strong");
    strong.textContent = message;
    output.appendChild(strong);
  }

  function rememberReturnedSeries(payload, state) {
    var found = [];
    collectSeries(payload, found, new Set(), 0);
    var existing = new Set(state.savedSeries.map(function (item) {
      return item.key;
    }));
    found.forEach(function (item) {
      if (!existing.has(item.key)) {
        existing.add(item.key);
        state.savedSeries.push(item);
      }
    });
    renderSavedSeries(state);
  }

  function buildEnvelope(apiVersion, toolName, toolVersion, argumentsValue) {
    if (
      typeof apiVersion !== "string" || !apiVersion
      || typeof toolName !== "string" || !toolName
      || typeof toolVersion !== "string" || !toolVersion
      || !argumentsValue || typeof argumentsValue !== "object"
      || Array.isArray(argumentsValue)
    ) {
      throw new Error("Tool, version, API version, and object arguments are required.");
    }
    var envelope = {
      api_version: apiVersion,
      tool: toolName,
      tool_version: toolVersion,
      arguments: argumentsValue
    };
    var serialized = stringifyStrictJson(envelope);
    if (serialized.length > MAX_ARGUMENT_CHARACTERS) {
      throw new Error("Tool request exceeds the browser request bound.");
    }
    return serialized;
  }

  async function requestJson(endpoint, options) {
    var response = await fetch(localEndpoint(endpoint), options);
    var source = await response.text();
    var payload = parseStrictJson(source);
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      throw new Error("The local endpoint returned an invalid JSON envelope.");
    }
    return { status: response.status, ok: response.ok, payload: payload };
  }

  function updateContractSummary(state, variant) {
    var tool = state.toolsByName.get(state.toolSelect.value);
    state.description.textContent = String(
      variant.description || tool.description || "Read-only public tool"
    );
    state.lifecycle.textContent = String(
      variant.lifecycle || tool.lifecycle || "active"
    );
    if (variant.deprecation && typeof variant.deprecation.message === "string") {
      state.lifecycle.textContent += " · " + variant.deprecation.message;
    }
    state.bounds.textContent = stringifyStrictJson(variant.workload_bounds || {});
  }

  function pinLatestVersion(state) {
    var tool = state.toolsByName.get(state.toolSelect.value);
    if (!tool) {
      throw new Error("Selected tool is not present in the current manifest.");
    }
    var selected = latestVariant(tool);
    if (!selected || typeof selected.version !== "string") {
      throw new Error("Selected tool does not advertise a semantic version.");
    }
    state.versionInput.value = selected.version;
    updateContractSummary(state, selected);
    renderFields(state.fields, selected, state);
    state.argumentsInput.value = stringifyStrictJson(firstExample(selected), 2);
    state.argumentsInput.setCustomValidity("");
    state.rawDirty = false;
    var location = new URL(window.location.href);
    location.pathname = "/agent-tools";
    location.search = new URLSearchParams({
      tool: state.toolSelect.value
    }).toString();
    window.history.replaceState({}, "", location.pathname + "?" + location.searchParams.toString());
  }

  function populateTools(state, manifest) {
    var originalTool = state.toolSelect.value;
    state.toolsByName.clear();
    state.toolSelect.replaceChildren();
    manifest.tools.forEach(function (tool) {
      if (!tool || typeof tool.name !== "string") {
        return;
      }
      state.toolsByName.set(tool.name, tool);
      state.toolSelect.appendChild(option(
        tool.name,
        tool.name,
        tool.name === originalTool
      ));
    });
    if (!state.toolsByName.size) {
      throw new Error("The current manifest did not declare any public tools.");
    }
    if (!state.toolsByName.has(state.toolSelect.value)) {
      state.toolSelect.value = state.toolsByName.keys().next().value;
    }
    pinLatestVersion(state);
  }

  async function initialize() {
    var form = document.querySelector("#current-tool-runner");
    if (!form) {
      return;
    }
    var state = {
      form: form,
      toolSelect: form.querySelector("[name=tool]"),
      versionInput: form.querySelector("[name=tool_version]"),
      apiVersion: form.querySelector("[name=api_version]"),
      fields: form.querySelector("[data-tool-fields]"),
      argumentsInput: form.querySelector("[name=arguments]"),
      output: document.querySelector("[data-tool-result]"),
      description: form.querySelector("[data-tool-description]"),
      lifecycle: form.querySelector("[data-tool-lifecycle]"),
      bounds: form.querySelector("[data-tool-bounds]"),
      savedSeriesPanel: form.querySelector("[data-saved-series]"),
      toolsByName: new Map(),
      savedSeries: [],
      rawDirty: false
    };
    if (
      !state.toolSelect || !state.versionInput || !state.apiVersion
      || !state.fields || !state.argumentsInput || !state.output
      || !state.description || !state.lifecycle || !state.bounds
      || !state.savedSeriesPanel
    ) {
      throw new Error("The Inspector tool runner markup is incomplete.");
    }

    setMessage(state.output, "loading", "Loading the current local tool manifest…");
    try {
      var manifestResponse = await requestJson(MANIFEST_ENDPOINT, {
        method: "GET",
        headers: { "Accept": "application/json" },
        credentials: "same-origin"
      });
      if (!manifestResponse.ok || !Array.isArray(manifestResponse.payload.tools)) {
        renderResult(state.output, manifestResponse.payload, manifestResponse.status);
        return;
      }
      state.apiVersion.value = String(manifestResponse.payload.api_version || "1.0");
      populateTools(state, manifestResponse.payload);
      renderSavedSeries(state);
      setMessage(state.output, "idle", "No tool request has run yet.");
    } catch (error) {
      setMessage(
        state.output,
        "error",
        error instanceof Error ? error.message : "The current manifest could not be loaded."
      );
      return;
    }

    state.toolSelect.addEventListener("change", function () {
      try {
        pinLatestVersion(state);
      } catch (error) {
        setMessage(state.output, "error", error instanceof Error ? error.message : "Tool selection failed.");
      }
    });
    state.fields.addEventListener("input", function () {
      state.rawDirty = false;
      synchronizeArguments(state);
    });
    state.fields.addEventListener("change", function () {
      state.rawDirty = false;
      synchronizeArguments(state);
    });
    state.argumentsInput.addEventListener("input", function () {
      state.rawDirty = true;
      state.argumentsInput.setCustomValidity("");
    });

    form.addEventListener("submit", async function (event) {
      event.preventDefault();
      try {
        var argumentsValue = state.rawDirty
          ? parseStrictJson(state.argumentsInput.value)
          : structuredArguments(state);
        if (
          !argumentsValue || typeof argumentsValue !== "object"
          || Array.isArray(argumentsValue)
        ) {
          throw new Error("Arguments must be one strict JSON object.");
        }
        var body = buildEnvelope(
          state.apiVersion.value,
          state.toolSelect.value,
          state.versionInput.value,
          argumentsValue
        );
        setMessage(state.output, "loading", "Running the bounded read-only tool…");
        var response = await requestJson(CALL_ENDPOINT, {
          method: "POST",
          headers: {
            "Accept": "application/json",
            "Content-Type": "application/json"
          },
          credentials: "same-origin",
          body: body
        });
        renderResult(state.output, response.payload, response.status);
        rememberReturnedSeries(response.payload, state);
      } catch (error) {
        setMessage(
          state.output,
          "error",
          error instanceof Error ? error.message : "The tool request failed."
        );
      }
    });
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = Object.freeze({
      buildEnvelope: buildEnvelope,
      collectSeries: function (value) {
        var output = [];
        collectSeries(value, output, new Set(), 0);
        return output;
      },
      parseStrictJson: parseStrictJson,
      latestVersion: function (tool) {
        var latest = latestVariant(tool);
        return String(latest && latest.version || "");
      },
      primaryType: primaryType,
      stringifyStrictJson: stringifyStrictJson
    });
  }

  if (typeof document !== "undefined") {
    document.addEventListener("DOMContentLoaded", function () {
      initialize().catch(function (error) {
        var output = document.querySelector("[data-tool-result]");
        if (output) {
          setMessage(
            output,
            "error",
            error instanceof Error ? error.message : "The tool UI could not start."
          );
        }
      });
    });
  }
}());
