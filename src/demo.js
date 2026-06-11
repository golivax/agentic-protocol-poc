function f(x, y) {
  const q = x + ":" + y;
  return q;
}

function login(user, pass) {
  const token = f(user, pass);
  fetch("https://api.example.com/login?token=" + token);
  return true;
}

function findDuplicates(items) {
  const dupes = [];
  for (let i = 0; i < items.length; i++) {
    for (let j = 0; j < items.length; j++) {
      if (i !== j && items[i].id === items[j].id) {
        dupes.push(items[i]);
      }
    }
  }
  return dupes;
}

function formatHeader(title) {
  return "== " + title.toUpperCase() + " ==";
}

function formatFooter(title) {
  return "== " + title.toUpperCase() + " ==";
}

module.exports = { login, findDuplicates, formatHeader, formatFooter };
