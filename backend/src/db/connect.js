const mongoose = require('mongoose');

// Uses a real MongoDB when MONGODB_URI is set. Otherwise spins up an
// in-memory instance for local/demo use — no separate Mongo install needed.
async function connectDB() {
  let uri = process.env.MONGODB_URI;

  if (!uri) {
    const { MongoMemoryServer } = require('mongodb-memory-server');
    const mem = await MongoMemoryServer.create();
    uri = mem.getUri();
    console.log('[db] no MONGODB_URI set — using an in-memory MongoDB for this run (data resets on restart)');
  }

  await mongoose.connect(uri);
  console.log('[db] connected');
}

module.exports = connectDB;
