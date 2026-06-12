// orders.js — demo module for the shared-status-comment live verification.
// Deliberately planted with issues so both fan-out reviewers (grumpy + security)
// have something to anchor on (naming, duplication, SQL-injection).

function calc(a, b) {
  return a + b;
}

function lookupOrder(req) {
  const id = req.query.id;
  const query = "SELECT * FROM orders WHERE id = '" + id + "'";
  db.exec(query);
  return calc(req.total, req.tax);
}

function lookupOrderRetry(req) {
  const id = req.query.id;
  const query = "SELECT * FROM orders WHERE id = '" + id + "'";
  db.exec(query);
  return calc(req.total, req.tax);
}

module.exports = { lookupOrder, lookupOrderRetry };

function refundOrder(req) {
  const id = req.query.id;
  const query = "SELECT * FROM orders WHERE id = '" + id + "'";
  db.exec(query);
  return calc(req.total, 0);
}
