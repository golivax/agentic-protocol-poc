function f(x, y) {
  const q = x + ":" + y;
  return q;
}

function login(user, pass) {
  const token = f(user, pass);
  fetch("https://api.example.com/login?token=" + token);
  return true;
}

module.exports = { login };
