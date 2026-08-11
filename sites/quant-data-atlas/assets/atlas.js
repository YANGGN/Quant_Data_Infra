(function () {
  "use strict";

  var MANIFEST_PATH = "data/manifest.json";
  var CHECKSUMS_PATH = "data/checksums.json";
  var SCHEMA_PATH = "data/schema.json";
  var MAX_MANIFEST_BYTES = 1024 * 1024;
  var MAX_CHECKSUMS_BYTES = 128 * 1024;
  var MAX_SCHEMA_BYTES = 128 * 1024;
  var MAX_CHUNK_BYTES = 262144;
  var MAX_TOTAL_BYTES = 8388608;
  var MAX_JSON_DEPTH = 32;
  var MAX_METADATA_DEPTH = 8;
  var MAX_JSON_ITEMS = 20000;
  var MAX_DATASETS = 4;
  var MAX_SOURCE_STORES = 4;
  var MAX_CHUNKS_PER_DATASET = 20;
  var MAX_TOTAL_CHUNKS = 48;
  var MAX_ROWS_PER_CHUNK = 250;
  var MAX_ROWS_PER_DATASET = 5000;
  var MAX_TOTAL_ROWS = 12000;
  var MAX_COLUMNS = 24;
  var MAX_SCHEMA_FIELDS = 64;
  var MAX_PUBLIC_FILES = 128;
  var PAGE_SIZE = 25;
  var EXPECTED_MANIFEST_KEYS = [
    "completeness", "contract", "cross_store_atomic", "cutoff", "datasets",
    "export_id", "files", "generated_at", "lifecycle", "point_in_time",
    "provenance", "registry", "revision_id", "semantic_dataset_id",
    "source_stores", "totals"
  ];
  var EXPECTED_DATASET_IDS = [
    "market-prices", "gdp-vintages", "company-issuers", "news-items"
  ];
  var EXPECTED_SOURCE_STORES = ["market", "macro", "company", "news"];
  var MAX_ROWS_BY_DATASET = {
    "market-prices": 5000,
    "gdp-vintages": 1000,
    "company-issuers": 1000,
    "news-items": 5000
  };
  var EXPECTED_DATASET_KEYS = [
    "bytes", "chunks", "completeness", "freshness", "id", "key_range",
    "label", "rows", "schema", "state"
  ];
  var EXPECTED_CHUNK_KEYS = ["bytes", "first_key", "last_key", "path", "rows", "sha256"];
  var EXPECTED_KEY_RANGE_KEYS = ["first_key", "last_key"];
  var EXPECTED_CHECKSUM_DOCUMENT_KEYS = ["files"];
  var EXPECTED_CHECKSUM_RECORD_KEYS = ["bytes", "path", "sha256"];
  var EXPECTED_SCHEMA_DOCUMENT_KEYS = ["export_id", "schemas", "version"];
  var EXPECTED_SCHEMA_KEYS = ["constraints", "fields", "id", "order_by", "row_identity"];
  var EXPECTED_SCHEMA_FIELD_KEYS = ["name", "nullable", "type"];
  var EXPECTED_ORDER_KEYS = ["direction", "field"];
  var EXPECTED_SCHEMA_CONSTRAINT_KEYS = [
    "additional_properties", "finite_numbers", "row_identity_unique", "total_order"
  ];
  var EXPECTED_TOTAL_KEYS = ["bounds", "bytes", "chunk_bytes", "rows"];
  var EXPECTED_TOTAL_BOUNDS_KEYS = ["max_bytes", "max_runtime_seconds", "max_total_rows"];
  var EXPECTED_SOURCE_STORE_KEYS = [
    "eligible_rows", "eligible_rows_sha256", "snapshot_at", "source_fingerprint",
    "source_logical_sha256", "store"
  ];
  var FORBIDDEN_PUBLIC_METADATA_KEYS = new Set([
    "artifact_id", "body", "credential", "database_path", "filesystem_path",
    "lock_key", "private_receipt", "raw_artifact", "run_id", "snapshot_id",
    "source_url", "sql"
  ]);
  var EXPECTED_STATIC_ASSET_PATHS = [
    "assets/INTER-OFL-1.1.txt",
    "assets/atlas.css",
    "assets/atlas.js",
    "assets/dashboard.css",
    "assets/inter-variable.woff2",
    "index.html"
  ];
  var EXPECTED_DATASET_LABELS = {
    "market-prices": "Market prices",
    "gdp-vintages": "GDP vintages",
    "company-issuers": "Company issuers",
    "news-items": "News items"
  };

  var REGISTERED_SCHEMA_LAYOUTS = {
    "market-prices": {
      "id": "atlas.fixture_snapshot.market_prices.v1",
      "fields": [
        ["instrument_id", "string", false], ["trade_date", "date", false],
        ["provider", "string", false], ["price_variant", "string", false],
        ["currency_segment", "string", false], ["open", "decimal_string", false],
        ["high", "decimal_string", false], ["low", "decimal_string", false],
        ["close", "decimal_string", false], ["volume", "integer", false],
        ["available_at", "temporal_string", false], ["available_precision", "temporal_precision", false],
        ["captured_at", "datetime", false], ["captured_precision", "temporal_precision", false],
        ["correction_sequence", "integer", false]
      ],
      "identity": ["instrument_id", "trade_date", "provider", "price_variant", "currency_segment"],
      "order": ["instrument_id", "trade_date", "provider", "price_variant", "currency_segment", "correction_sequence"]
    },
    "gdp-vintages": {
      "id": "atlas.fixture_snapshot.gdp_vintages.v1",
      "fields": [
        ["source", "string", false], ["source_vintage_identity", "string", false],
        ["release_stage", "string", true], ["vintage_at", "temporal_string", false],
        ["vintage_precision", "temporal_precision", false], ["source_published_at", "temporal_string", true],
        ["source_published_precision", "source_temporal_precision", false],
        ["available_at", "temporal_string", false], ["available_precision", "temporal_precision", false]
      ],
      "identity": ["source", "source_vintage_identity"],
      "order": ["source", "vintage_at", "source_vintage_identity"]
    },
    "company-issuers": {
      "id": "atlas.fixture_snapshot.company_issuers.v1",
      "fields": [
        ["issuer_id", "string", false], ["cik", "string", false],
        ["legal_name", "string", true], ["entity_type", "string", true],
        ["name_state", "string", false], ["missing_reason", "string", true],
        ["available_at", "temporal_string", false], ["available_precision", "temporal_precision", false],
        ["version_sequence", "integer", false]
      ],
      "identity": ["issuer_id"],
      "order": ["cik", "issuer_id", "version_sequence"]
    },
    "news-items": {
      "id": "atlas.fixture_snapshot.news_items.v1",
      "fields": [
        ["item_id", "string", false], ["source_name", "string", false],
        ["source_item_id", "string", false], ["source_kind", "string", false],
        ["headline", "string", true], ["published_at", "temporal_string", true],
        ["published_precision", "source_temporal_precision", false],
        ["content_state", "string", false], ["content_missing_reason", "string", true],
        ["item_state", "string", false], ["retraction_reason", "string", true],
        ["available_at", "temporal_string", false], ["available_precision", "temporal_precision", false],
        ["captured_at", "datetime", false], ["captured_precision", "temporal_precision", false],
        ["version_sequence", "integer", false]
      ],
      "identity": ["item_id"],
      "order": ["source_name", "source_item_id", "version_sequence"]
    }
  };

  function fail(message) {
    throw new Error(message);
  }

  function own(object, key) {
    return Object.prototype.hasOwnProperty.call(object, key);
  }

  function requireExactKeys(value, expected, label) {
    var actual = Object.keys(value).sort();
    var ordered = expected.slice().sort();
    if (actual.length !== ordered.length) {
      fail(label + " has an unexpected field set.");
    }
    for (var index = 0; index < ordered.length; index += 1) {
      if (actual[index] !== ordered[index]) {
        fail(label + " has an unexpected field set.");
      }
    }
  }

  function validateSha256(value, label) {
    requireText(value, label, 64);
    if (!/^[a-f0-9]{64}$/.test(value)) {
      fail(label + " must be a lowercase SHA-256 hex digest.");
    }
    return value;
  }

  function validateUtcInstant(value, label) {
    requireText(value, label, 160);
    if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/.test(value)) {
      fail(label + " must be an aware UTC timestamp.");
    }
    return value;
  }

  function utf8Length(source) {
    if (typeof TextEncoder !== "undefined") {
      return new TextEncoder().encode(source).byteLength;
    }
    var length = 0;
    for (var index = 0; index < source.length; index += 1) {
      var codeUnit = source.charCodeAt(index);
      if (codeUnit < 0x80) {
        length += 1;
      } else if (codeUnit < 0x800) {
        length += 2;
      } else if (codeUnit >= 0xd800 && codeUnit <= 0xdbff && index + 1 < source.length) {
        length += 4;
        index += 1;
      } else {
        length += 3;
      }
    }
    return length;
  }

  function StrictJsonParser(source, maxDepth) {
    this.source = source;
    this.index = 0;
    this.maxDepth = maxDepth;
  }

  StrictJsonParser.prototype.error = function (message) {
    fail("Strict JSON rejected: " + message + " at character " + (this.index + 1) + ".");
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
      this.error("expected " + character);
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
          this.error("invalid string");
        }
      }
      if (codeUnit <= 0x1f) {
        this.error("unescaped control character");
      }
      if (character === "\\") {
        this.index += 1;
        var escape = this.peek();
        if (escape === "u") {
          for (var offset = 1; offset <= 4; offset += 1) {
            if (!/^[0-9a-fA-F]$/.test(this.source.charAt(this.index + offset))) {
              this.error("invalid Unicode escape");
            }
          }
          this.index += 5;
          continue;
        }
        if ('"\\/bfnrt'.indexOf(escape) === -1) {
          this.error("invalid escape");
        }
        this.index += 1;
        continue;
      }
      this.index += 1;
    }
    this.error("unterminated string");
  };

  StrictJsonParser.prototype.parseNumber = function () {
    var start = this.index;
    if (this.peek() === "-") {
      this.index += 1;
    }
    if (this.peek() === "0") {
      this.index += 1;
      if (/^[0-9]$/.test(this.peek())) {
        this.error("leading zero");
      }
    } else if (/^[1-9]$/.test(this.peek())) {
      this.index += 1;
      while (/^[0-9]$/.test(this.peek())) {
        this.index += 1;
      }
    } else {
      this.error("invalid number");
    }
    if (this.peek() === ".") {
      this.index += 1;
      if (!/^[0-9]$/.test(this.peek())) {
        this.error("fraction requires a digit");
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
        this.error("exponent requires a digit");
      }
      while (/^[0-9]$/.test(this.peek())) {
        this.index += 1;
      }
    }
    if (!Number.isFinite(Number(this.source.slice(start, this.index)))) {
      this.error("numbers must be finite");
    }
  };

  StrictJsonParser.prototype.parseLiteral = function (literal) {
    if (this.source.slice(this.index, this.index + literal.length) !== literal) {
      this.error("invalid literal");
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
    var count = 0;
    while (true) {
      count += 1;
      if (count > MAX_JSON_ITEMS) {
        this.error("array item limit exceeded");
      }
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
    if (this.peek() === "}") {
      this.index += 1;
      return;
    }
    var keys = new Set();
    var count = 0;
    while (true) {
      if (this.peek() !== '"') {
        this.error("object key must be a string");
      }
      var key = this.parseString();
      if (keys.has(key)) {
        this.error("duplicate object key");
      }
      keys.add(key);
      count += 1;
      if (count > MAX_JSON_ITEMS) {
        this.error("object property limit exceeded");
      }
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
    if (depth > this.maxDepth) {
      this.error("nesting limit exceeded");
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

  function assertNoUnpairedSurrogate(value, label, depth) {
    if (depth > MAX_JSON_DEPTH) {
      fail(label + " exceeds the strict JSON nesting limit.");
    }
    if (typeof value === "string") {
      for (var index = 0; index < value.length; index += 1) {
        var codeUnit = value.charCodeAt(index);
        if (codeUnit >= 0xd800 && codeUnit <= 0xdbff) {
          var next = value.charCodeAt(index + 1);
          if (next < 0xdc00 || next > 0xdfff) {
            fail(label + " contains an unpaired Unicode surrogate.");
          }
          index += 1;
        } else if (codeUnit >= 0xdc00 && codeUnit <= 0xdfff) {
          fail(label + " contains an unpaired Unicode surrogate.");
        }
      }
      return;
    }
    if (typeof value === "number") {
      if (!Number.isFinite(value)) {
        fail(label + " contains a non-finite number.");
      }
      return;
    }
    if (Array.isArray(value)) {
      if (value.length > MAX_JSON_ITEMS) {
        fail(label + " exceeds the JSON item limit.");
      }
      for (var arrayIndex = 0; arrayIndex < value.length; arrayIndex += 1) {
        assertNoUnpairedSurrogate(value[arrayIndex], label, depth + 1);
      }
      return;
    }
    if (value && typeof value === "object") {
      var keys = Object.keys(value);
      if (keys.length > MAX_JSON_ITEMS) {
        fail(label + " exceeds the JSON property limit.");
      }
      for (var keyIndex = 0; keyIndex < keys.length; keyIndex += 1) {
        assertNoUnpairedSurrogate(keys[keyIndex], label, depth + 1);
        assertNoUnpairedSurrogate(value[keys[keyIndex]], label, depth + 1);
      }
    }
  }

  function parseStrictJson(source, maxBytes, label) {
    if (typeof source !== "string" || source.length === 0) {
      fail(label + " must be non-empty strict JSON.");
    }
    if (utf8Length(source) > maxBytes) {
      fail(label + " exceeds its byte limit.");
    }
    var parser = new StrictJsonParser(source, MAX_JSON_DEPTH);
    parser.skipWhitespace();
    parser.parseValue(0);
    parser.skipWhitespace();
    if (parser.index !== source.length) {
      fail("Strict JSON rejected: trailing content is not permitted.");
    }
    var parsed;
    try {
      parsed = JSON.parse(source);
    } catch (_error) {
      fail(label + " is not valid JSON.");
    }
    assertNoUnpairedSurrogate(parsed, label, 0);
    return parsed;
  }

  function requireObject(value, label) {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      fail(label + " must be an object.");
    }
    return value;
  }

  function requireText(value, label, maxLength) {
    if (typeof value !== "string" || value.trim().length === 0 || value.length > maxLength) {
      fail(label + " must be a bounded non-empty string.");
    }
    return value;
  }

  function requireBoundedWholeNumber(value, label, maximum) {
    if (!Number.isSafeInteger(value) || value < 0 || value > maximum) {
      fail(label + " must be a bounded whole number.");
    }
    return value;
  }

  function validatePublicMetadataKey(key, label) {
    requireText(key, label + " key", 160);
    if (FORBIDDEN_PUBLIC_METADATA_KEYS.has(key)) {
      fail(label + " contains a private or operational field.");
    }
  }

  function validateMetadata(value, label, depth) {
    if (depth > MAX_METADATA_DEPTH) {
      fail(label + " exceeds the metadata nesting limit.");
    }
    if (value === null || typeof value === "boolean") {
      return;
    }
    if (typeof value === "string") {
      requireText(value, label, 2048);
      return;
    }
    if (typeof value === "number") {
      if (!Number.isFinite(value)) {
        fail(label + " contains a non-finite number.");
      }
      return;
    }
    if (Array.isArray(value)) {
      if (value.length > MAX_SCHEMA_FIELDS) {
        fail(label + " exceeds the metadata item limit.");
      }
      for (var arrayIndex = 0; arrayIndex < value.length; arrayIndex += 1) {
        validateMetadata(value[arrayIndex], label, depth + 1);
      }
      return;
    }
    requireObject(value, label);
    var keys = Object.keys(value);
    if (keys.length > MAX_SCHEMA_FIELDS) {
      fail(label + " exceeds the metadata property limit.");
    }
    for (var keyIndex = 0; keyIndex < keys.length; keyIndex += 1) {
      validatePublicMetadataKey(keys[keyIndex], label);
      validateMetadata(value[keys[keyIndex]], label, depth + 1);
    }
  }

  function validateDataPath(value, label) {
    label = label || "Static data path";
    requireText(value, label, 256);
    if (value.slice(0, 5) !== "data/" || value.charAt(0) === "/" || value.indexOf("\\") !== -1) {
      fail(label + " must be a relative path under data/.");
    }
    if (!/^[A-Za-z0-9][A-Za-z0-9._/-]*$/.test(value)) {
      fail(label + " contains unsafe characters.");
    }
    var segments = value.split("/");
    for (var index = 0; index < segments.length; index += 1) {
      if (!/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(segments[index]) || segments[index] === "." || segments[index] === "..") {
        fail(label + " contains an unsafe segment.");
      }
    }
    return value;
  }

  function validatePublicPath(value, label) {
    label = label || "Static public path";
    requireText(value, label, 256);
    if (value.charAt(0) === "/" || value.indexOf("\\") !== -1 || !/^[A-Za-z0-9][A-Za-z0-9._/-]*$/.test(value)) {
      fail(label + " must be a relative same-origin static path.");
    }
    var segments = value.split("/");
    for (var index = 0; index < segments.length; index += 1) {
      if (!/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(segments[index]) || segments[index] === "." || segments[index] === "..") {
        fail(label + " contains an unsafe segment.");
      }
    }
    if (value !== "index.html" && value.slice(0, 7) !== "assets/" && value.slice(0, 5) !== "data/") {
      fail(label + " is outside the registered static snapshot.");
    }
    return value;
  }

  function validateDatasetId(value, label) {
    requireText(value, label, 128);
    if (!/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(value)) {
      fail(label + " must use a stable path-safe identifier.");
    }
    return value;
  }

  function validateIdentityKey(value, label, allowEmpty) {
    if (!Array.isArray(value) || value.length > MAX_COLUMNS || (!allowEmpty && value.length === 0)) {
      fail(label + " must be a bounded identity array.");
    }
    for (var index = 0; index < value.length; index += 1) {
      if (typeof value[index] === "string") {
        requireText(value[index], label + " component", 512);
      } else if (typeof value[index] === "number") {
        if (!Number.isFinite(value[index])) {
          fail(label + " contains a non-finite component.");
        }
      } else {
        fail(label + " components must be public strings or finite numbers.");
      }
    }
    return value;
  }

  function sameIdentityKey(left, right) {
    if (left.length !== right.length) {
      return false;
    }
    for (var index = 0; index < left.length; index += 1) {
      if (left[index] !== right[index]) {
        return false;
      }
    }
    return true;
  }

  function validateKeyRange(value, label, allowEmpty) {
    var range = requireObject(value, label);
    requireExactKeys(range, EXPECTED_KEY_RANGE_KEYS, label);
    var first = validateIdentityKey(range.first_key, label + " first key", allowEmpty);
    var last = validateIdentityKey(range.last_key, label + " last key", allowEmpty);
    if (allowEmpty) {
      if (first.length !== 0 || last.length !== 0) {
        fail(label + " for an empty dataset must use empty identity arrays.");
      }
      return range;
    }
    return range;
  }

  function validateChunk(value, label) {
    var chunk = requireObject(value, label);
    requireExactKeys(chunk, EXPECTED_CHUNK_KEYS, label);
    var path = validateDataPath(chunk.path, label + " path");
    if (path.slice(0, 12) !== "data/chunks/" || path.slice(-5) !== ".json") {
      fail(label + " path must be a JSON chunk under data/chunks/.");
    }
    validateSha256(chunk.sha256, label + " checksum");
    var rows = requireBoundedWholeNumber(chunk.rows, label + " rows", MAX_ROWS_PER_CHUNK);
    if (rows === 0) {
      fail(label + " must not declare an empty chunk.");
    }
    requireBoundedWholeNumber(chunk.bytes, label + " bytes", MAX_CHUNK_BYTES);
    var first = validateIdentityKey(chunk.first_key, label + " first key", false);
    var last = validateIdentityKey(chunk.last_key, label + " last key", false);
    return chunk;
  }

  function validateDataset(value, index, knownPaths, computed) {
    var dataset = requireObject(value, "Dataset " + (index + 1));
    requireExactKeys(dataset, EXPECTED_DATASET_KEYS, "Dataset " + (index + 1));
    var id = validateDatasetId(dataset.id, "Dataset id");
    if (id !== EXPECTED_DATASET_IDS[index]) {
      fail("Atlas datasets must use the registered deterministic order.");
    }
    requireText(dataset.label, "Dataset label", 256);
    requireText(dataset.state, "Dataset state", 80);
    requireText(dataset.completeness, "Dataset completeness", 80);
    validateMetadata(dataset.freshness, "Dataset freshness", 0);
    validateMetadata(dataset.schema, "Dataset schema", 0);
    if (!Array.isArray(dataset.chunks) || dataset.chunks.length > MAX_CHUNKS_PER_DATASET) {
      fail("Dataset chunks exceed the per-dataset limit.");
    }
    var declaredRows = requireBoundedWholeNumber(
      dataset.rows,
      "Dataset rows",
      MAX_ROWS_BY_DATASET[id]
    );
    var declaredBytes = requireBoundedWholeNumber(dataset.bytes, "Dataset bytes", MAX_TOTAL_BYTES);
    var keyRange = validateKeyRange(
      dataset.key_range,
      "Dataset key range",
      dataset.chunks.length === 0
    );
    var datasetRows = 0;
    var datasetBytes = 0;
    for (var chunkIndex = 0; chunkIndex < dataset.chunks.length; chunkIndex += 1) {
      var chunk = validateChunk(dataset.chunks[chunkIndex], "Dataset chunk " + (chunkIndex + 1));
      if (knownPaths.has(chunk.path)) {
        fail("Chunk paths must be unique across the snapshot.");
      }
      knownPaths.add(chunk.path);
      datasetRows += chunk.rows;
      datasetBytes += chunk.bytes;
      computed.chunks += 1;
      computed.rows += chunk.rows;
      computed.chunkBytes += chunk.bytes;
    }
    if (datasetRows !== declaredRows || datasetBytes !== declaredBytes) {
      fail("Dataset totals do not match its declared chunk inventory.");
    }
    if (datasetRows > MAX_ROWS_BY_DATASET[id] || datasetBytes > MAX_TOTAL_BYTES) {
      fail("Dataset exceeds its registered resource limits.");
    }
    if (dataset.chunks.length === 0) {
      if (declaredRows !== 0 || declaredBytes !== 0) {
        fail("An empty dataset cannot declare rows or bytes.");
      }
    } else if (
      !sameIdentityKey(keyRange.first_key, dataset.chunks[0].first_key)
      || !sameIdentityKey(keyRange.last_key, dataset.chunks[dataset.chunks.length - 1].last_key)
    ) {
      fail("Dataset key range does not match its chunk inventory.");
    }
    return dataset;
  }

  function validateSourceStore(value, index, datasets) {
    var source = requireObject(value, "Source store " + (index + 1));
    requireExactKeys(source, EXPECTED_SOURCE_STORE_KEYS, "Source store " + (index + 1));
    if (source.store !== EXPECTED_SOURCE_STORES[index]) {
      fail("Source stores must use the registered deterministic order.");
    }
    validateUtcInstant(source.snapshot_at, "Source store snapshot instant");
    validateSha256(source.source_logical_sha256, "Source logical fingerprint");
    if (
      typeof source.source_fingerprint !== "string"
      && (!source.source_fingerprint || typeof source.source_fingerprint !== "object")
    ) {
      fail("Source fingerprint must be bounded public metadata.");
    }
    validateMetadata(source.source_fingerprint, "Source fingerprint", 0);
    var eligibleRows = requireBoundedWholeNumber(
      source.eligible_rows,
      "Source eligible rows",
      MAX_ROWS_BY_DATASET[datasets[index].id]
    );
    if (eligibleRows !== datasets[index].rows) {
      fail("Source eligible rows do not match the declared dataset rows.");
    }
    validateSha256(source.eligible_rows_sha256, "Source eligible row fingerprint");
  }

  function validateManifest(value) {
    var manifest = requireObject(value, "Atlas manifest");
    requireExactKeys(manifest, EXPECTED_MANIFEST_KEYS, "Atlas manifest");
    if (manifest.export_id !== "atlas.fixture_snapshot") {
      fail("Atlas manifest must use the registered export id.");
    }
    validateSha256(manifest.revision_id, "Revision id");
    validateUtcInstant(manifest.generated_at, "Generated at");
    validateUtcInstant(manifest.cutoff, "Cutoff");
    if (manifest.semantic_dataset_id !== "atlas.fixture_snapshot.core_v1") {
      fail("Atlas manifest must use the registered semantic dataset id.");
    }
    requireText(manifest.completeness, "Manifest completeness", 80);
    if (manifest.completeness !== "complete") {
      fail("Atlas fixture profile cannot declare a partial scope.");
    }
    var lifecycle = requireObject(manifest.lifecycle, "Lifecycle");
    validateMetadata(lifecycle, "Lifecycle", 0);
    if (
      lifecycle.fixture_only !== true
      || lifecycle.hosting !== false
      || lifecycle.network !== false
      || lifecycle.mode !== "manual_only"
    ) {
      fail("Atlas lifecycle must remain fixture-only, manual, network-disabled, and hosting-disabled.");
    }
    var pointInTime = requireObject(manifest.point_in_time, "Point-in-time policy");
    validateMetadata(pointInTime, "Point-in-time policy", 0);
    if (
      pointInTime.availability !== "at_or_before"
      || pointInTime.date_only_policy !== "completed_date"
    ) {
      fail("Atlas point-in-time policy does not match the registered contract.");
    }
    var registry = requireObject(manifest.registry, "Registry");
    var contract = requireObject(manifest.contract, "Contract");
    var provenance = requireObject(manifest.provenance, "Provenance");
    validateMetadata(registry, "Registry", 0);
    validateMetadata(contract, "Contract", 0);
    validateMetadata(provenance, "Provenance", 0);
    if (manifest.cross_store_atomic !== false) {
      fail("Atlas must declare cross_store_atomic as false.");
    }
    if (!Array.isArray(manifest.datasets) || manifest.datasets.length !== MAX_DATASETS) {
      fail("Atlas must declare its four registered datasets.");
    }
    var knownPaths = new Set();
    var computed = { chunks: 0, rows: 0, chunkBytes: 0 };
    var datasets = [];
    for (var datasetIndex = 0; datasetIndex < manifest.datasets.length; datasetIndex += 1) {
      datasets.push(validateDataset(manifest.datasets[datasetIndex], datasetIndex, knownPaths, computed));
    }
    if (
      computed.chunks > MAX_TOTAL_CHUNKS
      || computed.rows > MAX_TOTAL_ROWS
      || computed.chunkBytes > MAX_TOTAL_BYTES
    ) {
      fail("Snapshot exceeds the Atlas resource limits.");
    }
    if (!Array.isArray(manifest.source_stores) || manifest.source_stores.length !== MAX_SOURCE_STORES) {
      fail("Atlas must declare its four source stores.");
    }
    for (var sourceIndex = 0; sourceIndex < manifest.source_stores.length; sourceIndex += 1) {
      validateSourceStore(manifest.source_stores[sourceIndex], sourceIndex, datasets);
    }
    var totals = requireObject(manifest.totals, "Manifest totals");
    validateMetadata(totals, "Manifest totals", 0);
    var declaredRows = requireBoundedWholeNumber(totals.rows, "Manifest rows", MAX_TOTAL_ROWS);
    var declaredChunkBytes = requireBoundedWholeNumber(
      totals.chunk_bytes,
      "Manifest chunk bytes",
      MAX_TOTAL_BYTES
    );
    var declaredBytes = requireBoundedWholeNumber(totals.bytes, "Manifest bytes", MAX_TOTAL_BYTES);
    if (
      declaredRows !== computed.rows
      || declaredChunkBytes !== computed.chunkBytes
      || declaredBytes < declaredChunkBytes
    ) {
      fail("Manifest totals do not match the declared dataset inventory.");
    }
    var files = requireObject(manifest.files, "Manifest files");
    requireExactKeys(files, ["count", "inventory_path"], "Manifest files");
    var fileCount = requireBoundedWholeNumber(files.count, "Manifest file count", MAX_PUBLIC_FILES);
    if (fileCount < knownPaths.size) {
      fail("Manifest file count cannot omit declared chunk files.");
    }
    if (validateDataPath(files.inventory_path, "Checksum inventory path") !== "data/checksums.json") {
      fail("Atlas must use the registered checksum inventory path.");
    }
    return manifest;
  }

  function lower(value) {
    return typeof value === "string" ? value.toLowerCase() : "";
  }

  function freshnessText(freshness) {
    if (typeof freshness === "string") {
      return freshness;
    }
    if (!freshness || typeof freshness !== "object") {
      return "Not declared";
    }
    var preferred = ["state", "status", "as_of", "available_at", "generated_at", "label"];
    var parts = [];
    for (var index = 0; index < preferred.length; index += 1) {
      if (own(freshness, preferred[index])) {
        parts.push(preferred[index].replace(/_/g, " ") + ": " + compactValue(freshness[preferred[index]], 120, 0));
      }
    }
    return parts.length ? parts.join(" · ") : "Declared metadata";
  }

  function datasetVisualState(dataset) {
    var state = lower(dataset.state);
    var completeness = lower(dataset.completeness);
    var freshness = lower(freshnessText(dataset.freshness));
    if (state.indexOf("unavailable") !== -1 || completeness.indexOf("unavailable") !== -1) {
      return "unavailable";
    }
    if (state.indexOf("error") !== -1) {
      return "error";
    }
    if (state.indexOf("truncat") !== -1 || completeness.indexOf("truncat") !== -1) {
      return "truncated";
    }
    if (state.indexOf("stale") !== -1 || freshness.indexOf("stale") !== -1) {
      return "stale";
    }
    if (dataset.chunks.length === 0) {
      return "empty";
    }
    return "ready";
  }

  async function readBoundedBytes(response, maximum, label) {
    if (response.headers && typeof response.headers.get === "function") {
      var contentLength = response.headers.get("content-length");
      if (contentLength && /^\d+$/.test(contentLength) && Number(contentLength) > maximum) {
        fail(label + " exceeds its byte limit.");
      }
    }
    if (response.body && typeof response.body.getReader === "function") {
      var reader = response.body.getReader();
      var parts = [];
      var total = 0;
      while (true) {
        var result = await reader.read();
        if (result.done) {
          break;
        }
        var value = result.value instanceof Uint8Array ? result.value : new Uint8Array(result.value);
        total += value.byteLength;
        if (total > maximum) {
          if (typeof reader.cancel === "function") {
            await reader.cancel();
          }
          fail(label + " exceeds its byte limit.");
        }
        parts.push(value);
      }
      var merged = new Uint8Array(total);
      var offset = 0;
      for (var index = 0; index < parts.length; index += 1) {
        merged.set(parts[index], offset);
        offset += parts[index].byteLength;
      }
      return merged;
    }
    if (typeof response.arrayBuffer !== "function") {
      fail(label + " has no readable body.");
    }
    var buffer = await response.arrayBuffer();
    var bytes = new Uint8Array(buffer);
    if (bytes.byteLength > maximum) {
      fail(label + " exceeds its byte limit.");
    }
    return bytes;
  }

  function decodeUtf8(bytes, label) {
    if (typeof TextDecoder === "undefined") {
      fail(label + " cannot be decoded in this browser.");
    }
    try {
      return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    } catch (_error) {
      fail(label + " is not valid UTF-8.");
    }
  }

  function validateStaticResponseUrl(response, safePath) {
    if (response.redirected) {
      fail("Static snapshot redirects are not permitted.");
    }
    if (typeof response.url !== "string" || response.url.length === 0) {
      return;
    }
    if (typeof URL === "undefined" || typeof window === "undefined" || !window.location) {
      fail("Static response origin cannot be verified.");
    }
    var expected = new URL(safePath, window.location.href);
    var received = new URL(response.url, window.location.href);
    if (expected.origin !== received.origin
      || expected.pathname !== received.pathname
      || received.search !== "" || received.hash !== "") {
      fail("Static snapshot response is cross-origin or redirected.");
    }
  }

  async function fetchStaticBytes(path, maximum, label) {
    var safePath = validateDataPath(path);
    if (typeof fetch !== "function") {
      fail("This browser cannot read the static Atlas snapshot.");
    }
    var response = await fetch(safePath, {
      method: "GET",
      headers: { "Accept": "application/json" },
      credentials: "same-origin",
      cache: "no-store",
      redirect: "error"
    });
    if (!response.ok) {
      fail(label + " could not be read from the static snapshot.");
    }
    validateStaticResponseUrl(response, safePath);
    return readBoundedBytes(response, maximum, label);
  }

  async function fetchStrictJson(path, maximum, label) {
    var bytes = await fetchStaticBytes(path, maximum, label);
    return {
      bytes: bytes,
      value: parseStrictJson(decodeUtf8(bytes, label), maximum, label)
    };
  }

  function sameJsonValue(left, right) {
    if (left === right) {
      return true;
    }
    if (typeof left !== typeof right || left === null || right === null) {
      return false;
    }
    if (Array.isArray(left) || Array.isArray(right)) {
      if (!Array.isArray(left) || !Array.isArray(right) || left.length !== right.length) {
        return false;
      }
      for (var arrayIndex = 0; arrayIndex < left.length; arrayIndex += 1) {
        if (!sameJsonValue(left[arrayIndex], right[arrayIndex])) {
          return false;
        }
      }
      return true;
    }
    if (typeof left === "object") {
      var leftKeys = Object.keys(left).sort();
      var rightKeys = Object.keys(right).sort();
      if (leftKeys.length !== rightKeys.length) {
        return false;
      }
      for (var keyIndex = 0; keyIndex < leftKeys.length; keyIndex += 1) {
        var key = leftKeys[keyIndex];
        if (key !== rightKeys[keyIndex] || !sameJsonValue(left[key], right[key])) {
          return false;
        }
      }
      return true;
    }
    return false;
  }

  function registeredSchema(datasetId) {
    var layout = REGISTERED_SCHEMA_LAYOUTS[datasetId];
    if (!layout) {
      fail("Atlas schema refers to an unregistered dataset.");
    }
    var fields = [];
    for (var fieldIndex = 0; fieldIndex < layout.fields.length; fieldIndex += 1) {
      fields.push({
        name: layout.fields[fieldIndex][0],
        type: layout.fields[fieldIndex][1],
        nullable: layout.fields[fieldIndex][2]
      });
    }
    var orderBy = [];
    for (var orderIndex = 0; orderIndex < layout.order.length; orderIndex += 1) {
      orderBy.push({ field: layout.order[orderIndex], direction: "asc" });
    }
    return {
      id: layout.id,
      fields: fields,
      row_identity: layout.identity.slice(),
      order_by: orderBy,
      constraints: {
        additional_properties: false,
        finite_numbers: "reject",
        row_identity_unique: true,
        total_order: true
      }
    };
  }

  function validateRegisteredSchema(value, datasetId, label) {
    var schema = requireObject(value, label);
    requireExactKeys(schema, EXPECTED_SCHEMA_KEYS, label);
    if (!Array.isArray(schema.fields) || !Array.isArray(schema.row_identity) || !Array.isArray(schema.order_by)) {
      fail(label + " has an invalid typed schema shape.");
    }
    for (var fieldIndex = 0; fieldIndex < schema.fields.length; fieldIndex += 1) {
      requireExactKeys(requireObject(schema.fields[fieldIndex], label + " field"), EXPECTED_SCHEMA_FIELD_KEYS, label + " field");
    }
    for (var orderIndex = 0; orderIndex < schema.order_by.length; orderIndex += 1) {
      requireExactKeys(requireObject(schema.order_by[orderIndex], label + " order"), EXPECTED_ORDER_KEYS, label + " order");
    }
    requireExactKeys(requireObject(schema.constraints, label + " constraints"), EXPECTED_SCHEMA_CONSTRAINT_KEYS, label + " constraints");
    if (!sameJsonValue(schema, registeredSchema(datasetId))) {
      fail(label + " does not match the registered typed schema.");
    }
    return schema;
  }

  function validateSchemaDocument(value) {
    var document = requireObject(value, "Atlas schema document");
    requireExactKeys(document, EXPECTED_SCHEMA_DOCUMENT_KEYS, "Atlas schema document");
    if (document.export_id !== "atlas.fixture_snapshot" || document.version !== "1.0.0") {
      fail("Atlas schema document does not match the registered export contract.");
    }
    if (!Array.isArray(document.schemas) || document.schemas.length !== MAX_DATASETS) {
      fail("Atlas schema document must contain the four registered schemas.");
    }
    var schemas = new Map();
    for (var index = 0; index < document.schemas.length; index += 1) {
      var datasetId = EXPECTED_DATASET_IDS[index];
      schemas.set(
        datasetId,
        validateRegisteredSchema(document.schemas[index], datasetId, "Atlas schema " + (index + 1))
      );
    }
    return schemas;
  }

  function expectedInventoryPaths(manifest) {
    var expected = new Set(EXPECTED_STATIC_ASSET_PATHS);
    expected.add(MANIFEST_PATH);
    expected.add(SCHEMA_PATH);
    for (var datasetIndex = 0; datasetIndex < manifest.datasets.length; datasetIndex += 1) {
      var dataset = manifest.datasets[datasetIndex];
      for (var chunkIndex = 0; chunkIndex < dataset.chunks.length; chunkIndex += 1) {
        var expectedPath = "data/chunks/" + dataset.id + "-" + String(chunkIndex + 1).padStart(4, "0") + ".json";
        if (dataset.chunks[chunkIndex].path !== expectedPath) {
          fail("Atlas chunk path does not match the registered deterministic inventory.");
        }
        expected.add(expectedPath);
      }
    }
    return expected;
  }

  function validateChecksumInventory(value, manifest) {
    var document = requireObject(value, "Atlas checksum inventory");
    requireExactKeys(document, EXPECTED_CHECKSUM_DOCUMENT_KEYS, "Atlas checksum inventory");
    if (!Array.isArray(document.files) || document.files.length === 0 || document.files.length >= MAX_PUBLIC_FILES) {
      fail("Atlas checksum inventory has an invalid file set.");
    }
    var expected = expectedInventoryPaths(manifest);
    if (document.files.length !== expected.size || manifest.files.count !== expected.size + 1) {
      fail("Atlas checksum inventory does not close the declared public file set.");
    }
    var records = new Map();
    var totalBytes = 0;
    for (var index = 0; index < document.files.length; index += 1) {
      var record = requireObject(document.files[index], "Atlas checksum record " + (index + 1));
      requireExactKeys(record, EXPECTED_CHECKSUM_RECORD_KEYS, "Atlas checksum record " + (index + 1));
      var path = validatePublicPath(record.path, "Atlas checksum path");
      if (path === CHECKSUMS_PATH || records.has(path)) {
        fail("Atlas checksum inventory contains a duplicate or self-referential path.");
      }
      validateSha256(record.sha256, "Atlas checksum digest");
      totalBytes += requireBoundedWholeNumber(record.bytes, "Atlas checksum byte count", MAX_TOTAL_BYTES);
      if (totalBytes > MAX_TOTAL_BYTES) {
        fail("Atlas checksum inventory exceeds the snapshot byte limit.");
      }
      records.set(path, record);
    }
    expected.forEach(function (path) {
      if (!records.has(path)) {
        fail("Atlas checksum inventory is missing a required public file.");
      }
    });
    records.forEach(function (_record, path) {
      if (!expected.has(path)) {
        fail("Atlas checksum inventory contains an unregistered public file.");
      }
    });
    for (var datasetIndex = 0; datasetIndex < manifest.datasets.length; datasetIndex += 1) {
      var chunks = manifest.datasets[datasetIndex].chunks;
      for (var chunkIndex = 0; chunkIndex < chunks.length; chunkIndex += 1) {
        var chunk = chunks[chunkIndex];
        var chunkRecord = records.get(chunk.path);
        if (!chunkRecord || chunkRecord.sha256 !== chunk.sha256 || chunkRecord.bytes !== chunk.bytes) {
          fail("Atlas checksum inventory does not match the manifest chunk inventory.");
        }
      }
    }
    return { records: records, totalBytes: totalBytes };
  }

  async function verifyInventoryRecordBytes(value, record, label) {
    var bytes = normalisedBytes(value);
    if (!record || bytes.byteLength !== record.bytes) {
      fail(label + " byte count does not match the checksum inventory.");
    }
    if (await sha256(bytes) !== record.sha256) {
      fail(label + " checksum does not match the checksum inventory.");
    }
  }

  async function loadVerifiedSnapshot() {
    var manifestResource = await fetchStrictJson(MANIFEST_PATH, MAX_MANIFEST_BYTES, "Atlas manifest");
    var manifest = validateManifest(manifestResource.value);
    var checksumsResource = await fetchStrictJson(CHECKSUMS_PATH, MAX_CHECKSUMS_BYTES, "Atlas checksum inventory");
    var inventory = validateChecksumInventory(checksumsResource.value, manifest);
    await verifyInventoryRecordBytes(manifestResource.bytes, inventory.records.get(MANIFEST_PATH), "Atlas manifest");
    var schemaResource = await fetchStrictJson(SCHEMA_PATH, MAX_SCHEMA_BYTES, "Atlas schema document");
    await verifyInventoryRecordBytes(schemaResource.bytes, inventory.records.get(SCHEMA_PATH), "Atlas schema document");
    var schemas = validateSchemaDocument(schemaResource.value);
    for (var datasetIndex = 0; datasetIndex < manifest.datasets.length; datasetIndex += 1) {
      var dataset = manifest.datasets[datasetIndex];
      var schema = schemas.get(dataset.id);
      if (!schema || !sameJsonValue(dataset.schema, schema)) {
        fail("Atlas manifest schema does not match the verified schema document.");
      }
    }
    if (
      inventory.totalBytes + checksumsResource.bytes.byteLength !== manifest.totals.bytes
      || manifest.totals.bytes > MAX_TOTAL_BYTES
    ) {
      fail("Atlas public byte total does not match the verified checksum inventory.");
    }
    return { manifest: manifest, inventory: inventory.records, schemas: schemas };
  }

  function normalisedBytes(value) {
    if (value instanceof Uint8Array) {
      return value;
    }
    if (value instanceof ArrayBuffer) {
      return new Uint8Array(value);
    }
    if (ArrayBuffer.isView(value)) {
      return new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
    }
    fail("Chunk bytes are not an ArrayBuffer.");
  }

  async function sha256(bytes) {
    if (!globalThis.crypto || !globalThis.crypto.subtle || typeof globalThis.crypto.subtle.digest !== "function") {
      fail("WebCrypto SHA-256 is required to verify Atlas chunks.");
    }
    var digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
    var hex = "";
    var values = new Uint8Array(digest);
    for (var index = 0; index < values.length; index += 1) {
      hex += values[index].toString(16).padStart(2, "0");
    }
    return hex;
  }

  function validateRows(rows, expectedRows) {
    if (!Array.isArray(rows) || rows.length !== expectedRows || rows.length > MAX_ROWS_PER_CHUNK) {
      fail("Chunk rows do not match the verified manifest inventory.");
    }
    for (var rowIndex = 0; rowIndex < rows.length; rowIndex += 1) {
      var row = rows[rowIndex];
      requireObject(row, "Chunk row " + (rowIndex + 1));
      var keys = Object.keys(row);
      if (keys.length > MAX_COLUMNS) {
        fail("Chunk rows exceed the column limit.");
      }
      for (var keyIndex = 0; keyIndex < keys.length; keyIndex += 1) {
        validateMetadata(row[keys[keyIndex]], "Chunk value", 0);
      }
    }
    return rows;
  }

  async function verifyChunkBytes(value, chunk) {
    validateChunk(chunk, "Chunk");
    var bytes = normalisedBytes(value);
    if (bytes.byteLength !== chunk.bytes || bytes.byteLength > MAX_CHUNK_BYTES) {
      fail("Chunk byte count does not match the verified manifest inventory.");
    }
    var actualDigest = await sha256(bytes);
    if (actualDigest !== lower(chunk.sha256)) {
      fail("Chunk checksum does not match the verified manifest inventory.");
    }
    var decoded = decodeUtf8(bytes, "Chunk");
    var parsed = parseStrictJson(decoded, MAX_CHUNK_BYTES, "Chunk");
    var rows = Array.isArray(parsed) ? parsed : parsed && parsed.rows;
    return validateRows(rows, chunk.rows);
  }

  function validCalendarDate(value) {
    var match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
    if (!match) {
      return false;
    }
    var year = Number(match[1]);
    var month = Number(match[2]);
    var day = Number(match[3]);
    if (month < 1 || month > 12 || day < 1) {
      return false;
    }
    var leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
    var days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
    return day <= days[month - 1];
  }

  function validInstant(value) {
    var match = /^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$/.exec(value);
    if (!match || !validCalendarDate(match[1])) {
      return false;
    }
    var hour = Number(match[2]);
    var minute = Number(match[3]);
    var second = Number(match[4]);
    if (hour > 23 || minute > 59 || second > 59) {
      return false;
    }
    if (match[5] !== "Z") {
      var zone = match[5].slice(1).split(":");
      if (Number(zone[0]) > 23 || Number(zone[1]) > 59) {
        return false;
      }
    }
    return true;
  }

  function validateTypedValue(value, field, label) {
    if (value === null) {
      if (!field.nullable) {
        fail(label + " violates a non-null typed field.");
      }
      return;
    }
    if (typeof value === "number" && !Number.isFinite(value)) {
      fail(label + " contains a non-finite number.");
    }
    if (typeof value === "boolean") {
      fail(label + " has a boolean type drift.");
    }
    if (field.type === "integer") {
      if (!Number.isSafeInteger(value)) {
        fail(label + " has an integer type drift.");
      }
      return;
    }
    if (field.type === "decimal_string") {
      if (
        typeof value !== "string"
        || !/^[+-]?(?:(?:\d+(?:\.\d*)?)|(?:\.\d+))(?:[eE][+-]?\d+)?$/.test(value)
      ) {
        fail(label + " has a finite decimal type drift.");
      }
      return;
    }
    if (typeof value !== "string") {
      fail(label + " has a string type drift.");
    }
    if (field.type === "string") {
      return;
    }
    if (field.type === "date" && validCalendarDate(value)) {
      return;
    }
    if (field.type === "datetime" && validInstant(value)) {
      return;
    }
    if (field.type === "temporal_string" && (validCalendarDate(value) || validInstant(value))) {
      return;
    }
    if (field.type === "temporal_precision" && (value === "date" || value === "datetime")) {
      return;
    }
    if (
      field.type === "source_temporal_precision"
      && (value === "date" || value === "datetime" || value === "unknown")
    ) {
      return;
    }
    fail(label + " has an invalid registered typed value.");
  }

  function compareTextCodePoints(left, right) {
    var leftIndex = 0;
    var rightIndex = 0;
    while (leftIndex < left.length && rightIndex < right.length) {
      var leftPoint = left.codePointAt(leftIndex);
      var rightPoint = right.codePointAt(rightIndex);
      if (leftPoint < rightPoint) {
        return -1;
      }
      if (leftPoint > rightPoint) {
        return 1;
      }
      leftIndex += leftPoint > 0xffff ? 2 : 1;
      rightIndex += rightPoint > 0xffff ? 2 : 1;
    }
    return leftIndex === left.length && rightIndex === right.length
      ? 0
      : (leftIndex === left.length ? -1 : 1);
  }

  function compareOrderAtom(left, right, label) {
    function atom(value) {
      if (value === null) {
        return { rank: 0, value: "" };
      }
      if (Number.isSafeInteger(value)) {
        return { rank: 1, value: value };
      }
      if (typeof value === "string") {
        return { rank: 2, value: value };
      }
      fail(label + " has an unsupported registered order value.");
    }
    var leftAtom = atom(left);
    var rightAtom = atom(right);
    if (leftAtom.rank !== rightAtom.rank) {
      return leftAtom.rank < rightAtom.rank ? -1 : 1;
    }
    if (leftAtom.rank === 1) {
      return leftAtom.value < rightAtom.value ? -1 : (leftAtom.value > rightAtom.value ? 1 : 0);
    }
    return leftAtom.rank === 2 ? compareTextCodePoints(leftAtom.value, rightAtom.value) : 0;
  }

  function compareRegisteredOrder(left, right, schema, label) {
    for (var orderIndex = 0; orderIndex < schema.order_by.length; orderIndex += 1) {
      var field = schema.order_by[orderIndex].field;
      var comparison = compareOrderAtom(left[field], right[field], label);
      if (comparison !== 0) {
        return comparison;
      }
    }
    return 0;
  }

  function createDatasetValidationState() {
    return {
      identities: new Set(),
      previousRow: null,
      firstKey: null,
      lastKey: null,
      rows: 0
    };
  }

  function validateTypedChunkRows(rows, chunk, schema, state) {
    var fieldNames = schema.fields.map(function (field) { return field.name; });
    var identity = schema.row_identity;
    var chunkFirst = null;
    var chunkLast = null;
    for (var rowIndex = 0; rowIndex < rows.length; rowIndex += 1) {
      var row = requireObject(rows[rowIndex], "Typed chunk row " + (rowIndex + 1));
      requireExactKeys(row, fieldNames, "Typed chunk row " + (rowIndex + 1));
      for (var fieldIndex = 0; fieldIndex < schema.fields.length; fieldIndex += 1) {
        var field = schema.fields[fieldIndex];
        validateTypedValue(row[field.name], field, "Typed chunk row " + (rowIndex + 1) + " " + field.name);
      }
      var key = identity.map(function (field) { return row[field]; });
      var encodedKey = JSON.stringify(key);
      if (state.identities.has(encodedKey)) {
        fail("Atlas chunk rows duplicate a registered row identity.");
      }
      state.identities.add(encodedKey);
      if (state.previousRow !== null && compareRegisteredOrder(state.previousRow, row, schema, "Atlas rows") >= 0) {
        fail("Atlas chunk rows are not in the registered total order.");
      }
      state.previousRow = row;
      if (chunkFirst === null) {
        chunkFirst = key;
      }
      chunkLast = key;
      if (state.firstKey === null) {
        state.firstKey = key;
      }
      state.lastKey = key;
      state.rows += 1;
    }
    if (!sameJsonValue(chunk.first_key, chunkFirst) || !sameJsonValue(chunk.last_key, chunkLast)) {
      fail("Atlas chunk key range does not match its verified rows.");
    }
  }

  function validateLoadedDataset(dataset, schema, state, actualBytes) {
    var expectedRange = {
      first_key: state.firstKey === null ? [] : state.firstKey,
      last_key: state.lastKey === null ? [] : state.lastKey
    };
    if (
      state.rows !== dataset.rows
      || actualBytes !== dataset.bytes
      || state.rows > MAX_ROWS_BY_DATASET[dataset.id]
      || actualBytes > MAX_TOTAL_BYTES
      || !sameJsonValue(dataset.key_range, expectedRange)
    ) {
      fail("Atlas dataset totals or key range do not match its verified chunks.");
    }
    return schema;
  }

  function compactValue(value, maximum, depth) {
    if (depth > 3) {
      return "[nested]";
    }
    if (value === null) {
      return "—";
    }
    if (typeof value === "string") {
      return value.length > maximum ? value.slice(0, maximum - 1) + "…" : value;
    }
    if (typeof value === "number" || typeof value === "boolean") {
      return String(value);
    }
    if (Array.isArray(value)) {
      var arrayValues = [];
      for (var arrayIndex = 0; arrayIndex < Math.min(value.length, 6); arrayIndex += 1) {
        arrayValues.push(compactValue(value[arrayIndex], 48, depth + 1));
      }
      return "[" + arrayValues.join(", ") + (value.length > 6 ? ", …]" : "]");
    }
    if (value && typeof value === "object") {
      var keys = Object.keys(value).slice(0, 6);
      var pairs = [];
      for (var keyIndex = 0; keyIndex < keys.length; keyIndex += 1) {
        pairs.push(keys[keyIndex] + ": " + compactValue(value[keys[keyIndex]], 36, depth + 1));
      }
      return "{" + pairs.join(", ") + (Object.keys(value).length > 6 ? ", …}" : "}");
    }
    return "—";
  }

  function element(tag, className, text) {
    var node = document.createElement(tag);
    if (className) {
      node.className = className;
    }
    if (text !== undefined) {
      node.textContent = text;
    }
    return node;
  }

  function clear(node) {
    while (node.firstChild) {
      node.removeChild(node.firstChild);
    }
  }

  function setText(id, value) {
    var node = document.getElementById(id);
    if (node) {
      node.textContent = value;
    }
  }

  function appendDefinition(list, label, value) {
    var term = element("dt", "", label);
    var description = element("dd", "", value);
    list.appendChild(term);
    list.appendChild(description);
  }

  var ui = {
    manifest: null,
    lastValid: null,
    inventory: null,
    lastValidInventory: null,
    schemas: null,
    lastValidSchemas: null,
    rowsByDataset: new Map(),
    selectedDatasetId: "",
    page: 1,
    manifestToken: 0,
    datasetToken: 0
  };

  function selectedDataset() {
    if (!ui.manifest) {
      return null;
    }
    for (var index = 0; index < ui.manifest.datasets.length; index += 1) {
      if (ui.manifest.datasets[index].id === ui.selectedDatasetId) {
        return ui.manifest.datasets[index];
      }
    }
    return null;
  }

  function setStatus(state, title, message) {
    var notice = document.getElementById("atlas-status");
    if (!notice) {
      return;
    }
    notice.className = "state-notice atlas-status" + (state === "error" ? " state-error" : "");
    notice.dataset.atlasState = state;
    clear(notice);
    notice.appendChild(element("h2", "", title));
    notice.appendChild(element("p", "", message));
    setText("atlas-shell-status", state === "ready" ? "Verified static snapshot" : title);
  }

  function setTableNotice(state, title, message) {
    var notice = document.getElementById("atlas-table-notice");
    if (!notice) {
      return;
    }
    notice.className = "state-notice" + (state === "error" ? " state-error" : "");
    notice.dataset.atlasState = state;
    clear(notice);
    notice.appendChild(element("h2", "", title));
    notice.appendChild(element("p", "", message));
  }

  function metadataLabel(value) {
    return compactValue(value, 220, 0);
  }

  function renderSourceStores(manifest) {
    var list = document.getElementById("atlas-source-stores");
    clear(list);
    if (manifest.source_stores.length === 0) {
      list.appendChild(element("li", "", "No public source-store metadata was declared."));
      return;
    }
    for (var index = 0; index < manifest.source_stores.length; index += 1) {
      var item = element("li");
      item.appendChild(element("span", "", metadataLabel(manifest.source_stores[index])));
      item.appendChild(element("strong", "", "Declared source"));
      list.appendChild(item);
    }
  }

  function renderDatasetStates(manifest) {
    var list = document.getElementById("atlas-dataset-states");
    clear(list);
    if (manifest.datasets.length === 0) {
      list.appendChild(element("li", "", "No public datasets were declared in this snapshot."));
      return;
    }
    for (var index = 0; index < manifest.datasets.length; index += 1) {
      var dataset = manifest.datasets[index];
      var state = datasetVisualState(dataset);
      var item = element("li");
      item.dataset.atlasState = state;
      item.appendChild(element("span", "", dataset.label + " — " + freshnessText(dataset.freshness)));
      item.appendChild(element("strong", "", state));
      list.appendChild(item);
    }
  }

  function renderManifestDetails(manifest) {
    setText("atlas-export-id", manifest.export_id);
    setText("atlas-revision-id", "Revision " + manifest.revision_id);
    setText("atlas-generated-at", manifest.generated_at);
    setText("atlas-cutoff", "Cutoff " + manifest.cutoff);
    setText("atlas-dataset-count", String(manifest.datasets.length));
    setText("atlas-registry", "Registry " + metadataLabel(manifest.registry));
    setText("atlas-source-count", String(manifest.source_stores.length));
    setText("atlas-cross-store", "Per-store cohort; not atomic");
    setText("atlas-cohort-summary", manifest.source_stores.length + " declared source store" + (manifest.source_stores.length === 1 ? "" : "s") + "; each snapshot instant is recorded independently.");
    setText("atlas-freshness-summary", "Dataset freshness, unavailable scope, and truncation remain explicit in this publication.");
    setText("atlas-non-atomic-note", "Cross-store scope: this snapshot declares cross_store_atomic: false. Its cohort records individual source-store snapshots; it is not one global database transaction or falsely unified as-of result.");
    renderSourceStores(manifest);
    renderDatasetStates(manifest);
    renderProvenance(manifest);
  }

  function renderProvenance(manifest) {
    var list = document.getElementById("atlas-provenance-details");
    clear(list);
    appendDefinition(list, "Export ID", manifest.export_id);
    appendDefinition(list, "Revision ID", manifest.revision_id);
    appendDefinition(list, "Generated at", manifest.generated_at);
    appendDefinition(list, "Availability cutoff", manifest.cutoff);
    appendDefinition(list, "Registry", metadataLabel(manifest.registry));
    appendDefinition(list, "Source stores", String(manifest.source_stores.length) + " declared public store receipt" + (manifest.source_stores.length === 1 ? "" : "s"));
    appendDefinition(list, "Cross-store atomic", "False — a declared best-effort per-store snapshot cohort.");
  }

  function renderDatasetOptions(manifest) {
    var select = document.getElementById("atlas-dataset-select");
    clear(select);
    if (manifest.datasets.length === 0) {
      var emptyOption = element("option", "", "No public datasets");
      emptyOption.value = "";
      select.appendChild(emptyOption);
      select.disabled = true;
      return;
    }
    for (var index = 0; index < manifest.datasets.length; index += 1) {
      var dataset = manifest.datasets[index];
      var option = element("option", "", dataset.label + " — " + datasetVisualState(dataset));
      option.value = dataset.id;
      select.appendChild(option);
    }
    select.disabled = false;
  }

  function renderSchema(dataset) {
    var list = document.getElementById("atlas-schema-details");
    clear(list);
    if (!dataset) {
      appendDefinition(list, "Status", "Awaiting a verified dataset.");
      return;
    }
    appendDefinition(list, "Dataset", dataset.label);
    if (typeof dataset.schema === "string") {
      appendDefinition(list, "Schema", dataset.schema);
      return;
    }
    var schema = dataset.schema;
    var preferred = ["id", "schema_id", "version", "digest", "description"];
    var used = new Set();
    for (var index = 0; index < preferred.length; index += 1) {
      if (own(schema, preferred[index])) {
        appendDefinition(list, preferred[index].replace(/_/g, " "), compactValue(schema[preferred[index]], 500, 0));
        used.add(preferred[index]);
      }
    }
    var fields = Array.isArray(schema.fields) ? schema.fields : [];
    if (fields.length) {
      for (var fieldIndex = 0; fieldIndex < fields.length; fieldIndex += 1) {
        var field = fields[fieldIndex];
        if (typeof field === "string") {
          appendDefinition(list, "Field", field);
        } else if (field && typeof field === "object") {
          appendDefinition(list, String(field.name || "Field"), compactValue(field, 500, 0));
        }
      }
      used.add("fields");
    }
    var keys = Object.keys(schema);
    for (var keyIndex = 0; keyIndex < keys.length; keyIndex += 1) {
      if (!used.has(keys[keyIndex])) {
        appendDefinition(list, keys[keyIndex].replace(/_/g, " "), compactValue(schema[keys[keyIndex]], 500, 0));
      }
    }
  }

  function tableColumns(dataset, rows) {
    var fromSchema = [];
    if (dataset && dataset.schema && typeof dataset.schema === "object" && Array.isArray(dataset.schema.fields)) {
      for (var fieldIndex = 0; fieldIndex < dataset.schema.fields.length; fieldIndex += 1) {
        var field = dataset.schema.fields[fieldIndex];
        var name = typeof field === "string" ? field : field && field.name;
        if (typeof name === "string" && name && fromSchema.indexOf(name) === -1) {
          fromSchema.push(name);
        }
      }
    }
    if (fromSchema.length) {
      return fromSchema.slice(0, MAX_COLUMNS);
    }
    var columns = [];
    for (var rowIndex = 0; rowIndex < rows.length && columns.length < MAX_COLUMNS; rowIndex += 1) {
      var keys = Object.keys(rows[rowIndex]).sort();
      for (var keyIndex = 0; keyIndex < keys.length && columns.length < MAX_COLUMNS; keyIndex += 1) {
        if (columns.indexOf(keys[keyIndex]) === -1) {
          columns.push(keys[keyIndex]);
        }
      }
    }
    return columns;
  }

  function renderSortOptions(columns) {
    var select = document.getElementById("atlas-sort");
    var previous = select.value;
    clear(select);
    var natural = element("option", "", "Snapshot order");
    natural.value = "";
    select.appendChild(natural);
    for (var index = 0; index < columns.length; index += 1) {
      var option = element("option", "", columns[index]);
      option.value = columns[index];
      select.appendChild(option);
    }
    select.value = columns.indexOf(previous) !== -1 ? previous : "";
    select.disabled = columns.length === 0;
  }

  function filteredRows(rows, columns) {
    var query = document.getElementById("atlas-search").value.trim().toLowerCase();
    var sortField = document.getElementById("atlas-sort").value;
    var result = [];
    for (var index = 0; index < rows.length; index += 1) {
      var row = rows[index];
      var matches = !query;
      if (!matches) {
        for (var columnIndex = 0; columnIndex < columns.length; columnIndex += 1) {
          if (compactValue(row[columns[columnIndex]], 240, 0).toLowerCase().indexOf(query) !== -1) {
            matches = true;
            break;
          }
        }
      }
      if (matches) {
        result.push({ row: row, index: index });
      }
    }
    if (sortField && columns.indexOf(sortField) !== -1) {
      result.sort(function (first, second) {
        var left = first.row[sortField];
        var right = second.row[sortField];
        if (typeof left === "number" && typeof right === "number") {
          return left === right ? first.index - second.index : left - right;
        }
        var comparison = compactValue(left, 240, 0).localeCompare(compactValue(right, 240, 0), undefined, { numeric: true, sensitivity: "base" });
        return comparison || first.index - second.index;
      });
    }
    return result;
  }

  function renderTable(dataset, rows) {
    var head = document.getElementById("atlas-table-head");
    var body = document.getElementById("atlas-table-body");
    var caption = document.getElementById("atlas-table-caption");
    clear(head);
    clear(body);
    var columns = tableColumns(dataset, rows);
    renderSortOptions(columns);
    var visibleRows = filteredRows(rows, columns);
    var pageCount = Math.max(1, Math.ceil(visibleRows.length / PAGE_SIZE));
    ui.page = Math.max(1, Math.min(ui.page, pageCount));
    var start = (ui.page - 1) * PAGE_SIZE;
    var end = Math.min(start + PAGE_SIZE, visibleRows.length);
    caption.textContent = dataset.label + ": " + visibleRows.length + " verified loaded row" + (visibleRows.length === 1 ? "" : "s") + ".";

    if (!columns.length) {
      var emptyRow = element("tr");
      var emptyCell = element("td", "", "This verified dataset contains no visible fields.");
      emptyCell.colSpan = 1;
      emptyRow.appendChild(emptyCell);
      body.appendChild(emptyRow);
    } else {
      var headerRow = element("tr");
      for (var columnIndex = 0; columnIndex < columns.length; columnIndex += 1) {
        var header = element("th", "", columns[columnIndex]);
        header.scope = "col";
        headerRow.appendChild(header);
      }
      head.appendChild(headerRow);
      for (var rowIndex = start; rowIndex < end; rowIndex += 1) {
        var source = visibleRows[rowIndex].row;
        var tableRow = element("tr");
        for (var cellIndex = 0; cellIndex < columns.length; cellIndex += 1) {
          var cellValue = source[columns[cellIndex]];
          var cell = element("td", "atlas-cell", compactValue(cellValue, 360, 0));
          if (typeof cellValue === "number") {
            cell.className += " atlas-cell--numeric";
          }
          tableRow.appendChild(cell);
        }
        body.appendChild(tableRow);
      }
    }
    setText("atlas-pagination-summary", visibleRows.length ? "Showing " + (start + 1) + "–" + end + " of " + visibleRows.length + " loaded row" + (visibleRows.length === 1 ? "" : "s") + "." : "No loaded rows match the current search.");
    document.getElementById("atlas-prev-page").disabled = ui.page <= 1 || visibleRows.length === 0;
    document.getElementById("atlas-next-page").disabled = ui.page >= pageCount || visibleRows.length === 0;
  }

  function renderEmptyTable(message) {
    var head = document.getElementById("atlas-table-head");
    var body = document.getElementById("atlas-table-body");
    clear(head);
    clear(body);
    var row = element("tr");
    var cell = element("td", "", message);
    cell.colSpan = 1;
    row.appendChild(cell);
    body.appendChild(row);
    setText("atlas-table-caption", message);
    setText("atlas-pagination-summary", "No rows available.");
    document.getElementById("atlas-prev-page").disabled = true;
    document.getElementById("atlas-next-page").disabled = true;
  }

  async function loadSelectedDataset() {
    var dataset = selectedDataset();
    ui.page = 1;
    renderSchema(dataset);
    if (!dataset) {
      setTableNotice("empty", "No public dataset selected", "This snapshot currently exposes no selectable public dataset.");
      renderEmptyTable("No verified dataset selected.");
      return;
    }
    var visualState = datasetVisualState(dataset);
    setText("atlas-dataset-description", dataset.label + " — declared state: " + dataset.state + "; completeness: " + dataset.completeness + "; freshness: " + freshnessText(dataset.freshness) + ".");
    if (visualState === "unavailable" || visualState === "error") {
      setTableNotice(visualState, dataset.label + " is " + visualState, "The manifest declares this dataset unavailable. Atlas will not fabricate rows or substitute a live source.");
      renderEmptyTable(dataset.label + " is unavailable in this snapshot.");
      return;
    }
    if (ui.rowsByDataset.has(dataset.id)) {
      setTableNotice(visualState, dataset.label + " verified", visualState === "truncated" ? "Only the explicitly declared truncated snapshot rows are available." : "Verified static chunks are loaded locally in this page.");
      renderTable(dataset, ui.rowsByDataset.get(dataset.id));
      return;
    }
    var token = ++ui.datasetToken;
    setTableNotice("loading", "Verifying " + dataset.label, "Reading only declared static chunks and checking SHA-256 before rendering.");
    renderEmptyTable("Verifying static chunks…");
    try {
      var schema = ui.schemas && ui.schemas.get(dataset.id);
      if (!schema || !ui.inventory) {
        fail("Atlas dataset has no verified schema or checksum inventory.");
      }
      var validation = createDatasetValidationState();
      var actualBytes = 0;
      var allRows = [];
      for (var chunkIndex = 0; chunkIndex < dataset.chunks.length; chunkIndex += 1) {
        var chunk = dataset.chunks[chunkIndex];
        var inventoryRecord = ui.inventory.get(chunk.path);
        if (!inventoryRecord || inventoryRecord.sha256 !== chunk.sha256 || inventoryRecord.bytes !== chunk.bytes) {
          fail("Atlas chunk is not bound to the verified checksum inventory.");
        }
        var bytes = await fetchStaticBytes(chunk.path, chunk.bytes, "Dataset chunk");
        await verifyInventoryRecordBytes(bytes, inventoryRecord, "Dataset chunk");
        var rows = await verifyChunkBytes(bytes, chunk);
        validateTypedChunkRows(rows, chunk, schema, validation);
        actualBytes += bytes.byteLength;
        if (allRows.length + rows.length > MAX_ROWS_PER_DATASET) {
          fail("Dataset rows exceed the Atlas rendering limit.");
        }
        for (var rowIndex = 0; rowIndex < rows.length; rowIndex += 1) {
          allRows.push(rows[rowIndex]);
        }
      }
      validateLoadedDataset(dataset, schema, validation, actualBytes);
      if (token !== ui.datasetToken || dataset !== selectedDataset()) {
        return;
      }
      ui.rowsByDataset.set(dataset.id, allRows);
      setTableNotice(visualState, dataset.label + " verified", visualState === "truncated" ? "Only the explicitly declared truncated snapshot rows are rendered." : "Chunk checksums, byte counts, row counts, and strict JSON all passed.");
      renderTable(dataset, allRows);
    } catch (error) {
      if (token !== ui.datasetToken) {
        return;
      }
      var message = error instanceof Error ? error.message : "The static chunk could not be verified.";
      setTableNotice("error", "Dataset verification failed", message + " The table remains empty rather than showing unverified data.");
      renderEmptyTable("Dataset verification failed.");
    }
  }

  function enableControls(enabled) {
    document.getElementById("atlas-dataset-select").disabled = !enabled || !ui.manifest || ui.manifest.datasets.length === 0;
    document.getElementById("atlas-search").disabled = !enabled;
    document.getElementById("atlas-sort").disabled = !enabled;
    document.getElementById("atlas-reload").disabled = !enabled;
  }

  async function loadManifest() {
    var token = ++ui.manifestToken;
    enableControls(false);
    setStatus("loading", "Loading static snapshot", "Reading only data/manifest.json from this Atlas publication.");
    try {
      var snapshot = await loadVerifiedSnapshot();
      var manifest = snapshot.manifest;
      if (token !== ui.manifestToken) {
        return;
      }
      var priorDatasetId = ui.selectedDatasetId;
      ui.manifest = manifest;
      ui.lastValid = manifest;
      ui.inventory = snapshot.inventory;
      ui.lastValidInventory = snapshot.inventory;
      ui.schemas = snapshot.schemas;
      ui.lastValidSchemas = snapshot.schemas;
      ui.rowsByDataset.clear();
      renderManifestDetails(manifest);
      renderDatasetOptions(manifest);
      if (manifest.datasets.some(function (dataset) { return dataset.id === priorDatasetId; })) {
        ui.selectedDatasetId = priorDatasetId;
      } else {
        ui.selectedDatasetId = manifest.datasets.length ? manifest.datasets[0].id : "";
      }
      document.getElementById("atlas-dataset-select").value = ui.selectedDatasetId;
      enableControls(true);
      setStatus("ready", "Static snapshot verified", "The manifest passed strict JSON, bounded resource, relative-path, and public-cohort validation. Dataset chunks remain unshown until independently verified.");
      await loadSelectedDataset();
    } catch (error) {
      if (token !== ui.manifestToken) {
        return;
      }
      var message = error instanceof Error ? error.message : "The static manifest could not be verified.";
      if (ui.lastValid) {
        ui.manifest = ui.lastValid;
        enableControls(true);
        setStatus("error", "Current snapshot rejected; last valid snapshot retained", message + " The previously verified snapshot remains visible in this page and no operational fallback is attempted.");
      } else {
        ui.manifest = null;
        enableControls(false);
        setStatus("error", "No verified snapshot available", message + " Atlas will not display unverified or live fallback data.");
        setTableNotice("error", "No verified dataset available", "A last-valid snapshot is not available in this page.");
        renderEmptyTable("No verified snapshot is available.");
        renderSchema(null);
      }
    }
  }

  function bindControls() {
    document.getElementById("atlas-reload").addEventListener("click", function () {
      loadManifest();
    });
    document.getElementById("atlas-dataset-select").addEventListener("change", function (event) {
      ui.selectedDatasetId = event.target.value;
      ui.datasetToken += 1;
      loadSelectedDataset();
    });
    document.getElementById("atlas-search").addEventListener("input", function () {
      var dataset = selectedDataset();
      if (dataset && ui.rowsByDataset.has(dataset.id)) {
        ui.page = 1;
        renderTable(dataset, ui.rowsByDataset.get(dataset.id));
      }
    });
    document.getElementById("atlas-sort").addEventListener("change", function () {
      var dataset = selectedDataset();
      if (dataset && ui.rowsByDataset.has(dataset.id)) {
        ui.page = 1;
        renderTable(dataset, ui.rowsByDataset.get(dataset.id));
      }
    });
    document.getElementById("atlas-prev-page").addEventListener("click", function () {
      var dataset = selectedDataset();
      if (dataset && ui.rowsByDataset.has(dataset.id) && ui.page > 1) {
        ui.page -= 1;
        renderTable(dataset, ui.rowsByDataset.get(dataset.id));
      }
    });
    document.getElementById("atlas-next-page").addEventListener("click", function () {
      var dataset = selectedDataset();
      if (dataset && ui.rowsByDataset.has(dataset.id)) {
        ui.page += 1;
        renderTable(dataset, ui.rowsByDataset.get(dataset.id));
      }
    });
    document.getElementById("atlas-controls").addEventListener("submit", function (event) {
      event.preventDefault();
    });
  }

  function bootstrap() {
    bindControls();
    loadManifest();
  }

  var publicApi = Object.freeze({
    MAX_CHUNK_BYTES: MAX_CHUNK_BYTES,
    MAX_MANIFEST_BYTES: MAX_MANIFEST_BYTES,
    MAX_TOTAL_BYTES: MAX_TOTAL_BYTES,
    MAX_DATASETS: MAX_DATASETS,
    MAX_CHUNKS_PER_DATASET: MAX_CHUNKS_PER_DATASET,
    MAX_TOTAL_CHUNKS: MAX_TOTAL_CHUNKS,
    MAX_ROWS_PER_CHUNK: MAX_ROWS_PER_CHUNK,
    MAX_ROWS_PER_DATASET: MAX_ROWS_PER_DATASET,
    MAX_TOTAL_ROWS: MAX_TOTAL_ROWS,
    fetchStaticBytes: fetchStaticBytes,
    loadVerifiedSnapshot: loadVerifiedSnapshot,
    parseStrictJson: parseStrictJson,
    validateChecksumInventory: validateChecksumInventory,
    validateDataPath: validateDataPath,
    validateManifest: validateManifest,
    validatePublicPath: validatePublicPath,
    validateSchemaDocument: validateSchemaDocument,
    validateStaticResponseUrl: validateStaticResponseUrl,
    validateTypedChunkRows: validateTypedChunkRows,
    verifyChunkBytes: verifyChunkBytes
  });

  if (typeof module !== "undefined" && module.exports) {
    module.exports = publicApi;
  }

  if (typeof document !== "undefined") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", bootstrap, { once: true });
    } else {
      bootstrap();
    }
  }
}());
