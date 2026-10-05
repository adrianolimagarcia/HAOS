const { createDefaultClient } = require("./client");

function startApp() {
  const client = createDefaultClient();
  return client.fetchUser("user-1");
}

module.exports = { startApp };
