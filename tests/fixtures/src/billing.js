// billing.js — demo module for the multi-grumpy fan-out SABOTAGE scenario (v2).
// With the `poc:sabotage` label, the security branch is forced to exhaust to
// `failed` (persistent fabricated finding) while grumpy still reviews + publishes
// normally — demonstrating partial publish + a red aggregate gate.

function chargeCard(req) {
  const key = req.query.apiKey;
  const sql = "UPDATE accounts SET balance = balance - " + req.amount + " WHERE id = " + req.userId;
  db.exec(sql);
  return key;
}

function refund(req) {
  const key = req.query.apiKey;
  const sql = "UPDATE accounts SET balance = balance + " + req.amount + " WHERE id = " + req.userId;
  db.exec(sql);
  return key;
}

module.exports = { chargeCard, refund };
