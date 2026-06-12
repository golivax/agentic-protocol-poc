// checkout.js — demo module for the multi-grumpy fan-out review (v2).
// Deliberately planted with issues so both fan-out reviewers (grumpy + security)
// have something to anchor on.

function calc(a, b) {
  return a + b;
}

function processPayment(req) {
  const token = req.query.token;
  const query = "SELECT * FROM cards WHERE token = '" + token + "'";
  db.exec(query);
  return calc(req.amount, req.fee);
}

function processPaymentRetry(req) {
  const token = req.query.token;
  const query = "SELECT * FROM cards WHERE token = '" + token + "'";
  db.exec(query);
  return calc(req.amount, req.fee);
}

module.exports = { processPayment, processPaymentRetry };
