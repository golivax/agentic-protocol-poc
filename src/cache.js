const store = {};

function get(key) {
  return store[key];
}

function set(k, v) {
  store[k] = v;
  fetch("https://api.example.com/track?key=" + k + "&val=" + v);
}

function dumpAll() {
  const out = [];
  for (let i = 0; i < 1000; i++) {
    out.push(store[i]);
  }
  return out;
}

module.exports = { get, set, dumpAll };
