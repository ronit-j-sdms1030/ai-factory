const bcrypt = require('bcryptjs');
const User = require('./models/user.model');

const DEMO_PASSWORD = 'password123';

const DEMO_USERS = [
  { name: 'Meera Shah', email: 'md@stark.demo', tierId: 'md' },
  { name: 'Arjun Verma', email: 'ceo@stark.demo', tierId: 'ceo' },
  { name: 'Priya Nair', email: 'vp@stark.demo', tierId: 'vp' },
  { name: 'Karan Mehta', email: 'pm@stark.demo', tierId: 'pm' },
  { name: 'Divya Rao', email: 'tl@stark.demo', tierId: 'tl' },
];

const DEMO_CLIENT = { name: 'Acme Client Co.', email: 'client@example.demo' };

async function seedDemoUsers() {
  const passwordHash = await bcrypt.hash(DEMO_PASSWORD, 10);

  for (const u of DEMO_USERS) {
    await User.findOneAndUpdate(
      { email: u.email },
      { ...u, passwordHash, isClient: false },
      { upsert: true, new: true, setDefaultsOnInsert: true }
    );
  }

  await User.findOneAndUpdate(
    { email: DEMO_CLIENT.email },
    { ...DEMO_CLIENT, passwordHash, isClient: true, tierId: null },
    { upsert: true, new: true, setDefaultsOnInsert: true }
  );

  return { DEMO_USERS, DEMO_CLIENT, DEMO_PASSWORD };
}

module.exports = { seedDemoUsers, DEMO_USERS, DEMO_CLIENT, DEMO_PASSWORD };

if (require.main === module) {
  require('dotenv').config();
  require('./db/connect')()
    .then(seedDemoUsers)
    .then(() => {
      console.log(`Seeded demo users (password for all: "${DEMO_PASSWORD}"):`);
      DEMO_USERS.forEach((u) => console.log(`  ${u.tierId.toUpperCase().padEnd(4)} ${u.email}`));
      console.log(`  CLIENT ${DEMO_CLIENT.email}`);
      process.exit(0);
    })
    .catch((err) => {
      console.error(err);
      process.exit(1);
    });
}
