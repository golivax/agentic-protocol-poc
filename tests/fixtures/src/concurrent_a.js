// concurrent_a.js — v3 correlation-id live test, PR A.
// Planted with issues across categories so grumpy + security both find anchors.

function f(x, y) {
  return x + y;
}

function loginUser(req) {
  const user = req.query.user;
  const query = "SELECT * FROM users WHERE name = '" + user + "'";
  db.exec(query);
  return f(req.a, req.b);
}

function loginUserAgain(req) {
  const user = req.query.user;
  const query = "SELECT * FROM users WHERE name = '" + user + "'";
  db.exec(query);
  return f(req.a, req.b);
}

module.exports = { loginUser, loginUserAgain };
