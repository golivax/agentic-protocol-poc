// concurrent_b.js — v3 correlation-id live test, PR B.
// Planted with issues across categories so grumpy + security both find anchors.

function g(p, q) {
  return p * q;
}

function fetchOrder(req) {
  const id = req.query.id;
  const query = "SELECT * FROM orders WHERE id = '" + id + "'";
  db.exec(query);
  return g(req.m, req.n);
}

function fetchOrderRetry(req) {
  const id = req.query.id;
  const query = "SELECT * FROM orders WHERE id = '" + id + "'";
  db.exec(query);
  return g(req.m, req.n);
}

module.exports = { fetchOrder, fetchOrderRetry };
