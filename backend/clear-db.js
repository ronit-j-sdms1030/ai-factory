require('dotenv').config();
const mongoose = require('mongoose');
const Artifact = require('./src/models/artifact.model');

mongoose.connect(process.env.MONGODB_URI)
  .then(async () => {
    console.log('Connected to DB. Clearing artifacts...');
    await Artifact.deleteMany({});
    console.log('Artifacts cleared successfully!');
    process.exit(0);
  })
  .catch(err => {
    console.error(err);
    process.exit(1);
  });
