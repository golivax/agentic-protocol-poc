const store = {};

function get(key) {
  if (key === null || key === undefined) {
    throw new Error("key is required");
  }
  return store[key];
}

function set(key, value) {
  store[key] = value;
}

module.exports = { get, set };
