const mongoose = require('mongoose');

async function migrate() {
  const [sourceUri, targetUri] = process.argv.slice(2);
  if (!sourceUri || !targetUri) throw new Error('Usage: node scripts/migrate-mongo.js <source-uri> <target-uri>');

  const source = mongoose.createConnection(sourceUri);
  const target = mongoose.createConnection(targetUri);
  await Promise.all([source.asPromise(), target.asPromise()]);

  const collections = await source.db.listCollections().toArray();
  const summary = [];
  for (const { name } of collections) {
    const documents = await source.db.collection(name).find({}).toArray();
    const destination = target.db.collection(name);
    await destination.deleteMany({});
    if (documents.length) await destination.insertMany(documents);
    const copied = await destination.countDocuments();
    if (copied !== documents.length) throw new Error(`${name}: expected ${documents.length}, copied ${copied}`);
    summary.push({ collection: name, copied });
  }

  console.log(JSON.stringify(summary));
  await Promise.all([source.close(), target.close()]);
}

migrate().catch((err) => {
  console.error(err.message);
  process.exit(1);
});
