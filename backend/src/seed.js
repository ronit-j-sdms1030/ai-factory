const bcrypt = require('bcryptjs');
const User = require('./models/user.model');

const DEMO_PASSWORD = 'password123';

const DEMO_USERS = [
  { name: 'Amit Mohol', email: 'amit@stark.demo', tierId: 'md' },
  { name: 'Kartik Shete', email: 'kartik@stark.demo', tierId: 'ceo' },
  { name: 'Ashwini Bankar', email: 'ashwini@stark.demo', tierId: 'vp' },
  { name: 'Karan Mehta', email: 'pm@stark.demo', tierId: 'pm' },
  { name: 'Siddhesh', email: 'siddhesh@stark.demo', tierId: 'tl', department: 'AI' },
  { name: 'Mandar', email: 'mandar@stark.demo', tierId: 'tl', department: 'AI' },
  { name: 'Shubham', email: 'shubham@stark.demo', tierId: 'tl', department: 'Development' },
  { name: 'Adarsh', email: 'adarsh@stark.demo', tierId: 'tl', department: 'Development' },
  { name: 'Pallav', email: 'pallav@stark.demo', tierId: 'tl', department: 'Sales & Marketing' },
  // QA and DevOps are two of the five departments split_team_reports can
  // assign work to (see TEAM_DEPARTMENTS in llm.service.js) but had no TL
  // account — a package landing on either was invisible to everyone, since
  // GET / scopes a TL's visibility to teamReports.team === their own
  // department and nobody had that department set.
  { name: 'Rehan', email: 'rehan@stark.demo', tierId: 'tl', department: 'QA' },
  { name: 'Neha', email: 'neha@stark.demo', tierId: 'tl', department: 'DevOps' },
];

const DEMO_CLIENT = { name: 'Acme Client Co.', email: 'client@example.demo' };

async function seedDemoUsers() {
  const passwordHash = await bcrypt.hash(DEMO_PASSWORD, 10);

  // Replaced by named logins (tl@stark.demo -> 5 named TLs; md/ceo/vp@stark.demo -> real names).
  await User.deleteMany({ email: { $in: ['tl@stark.demo', 'md@stark.demo', 'ceo@stark.demo', 'vp@stark.demo'] } });

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
      DEMO_USERS.forEach((u) => console.log(`  ${u.tierId.toUpperCase().padEnd(4)} ${u.email}${u.department ? ' (' + u.department + ')' : ''} — ${u.name}`));
      console.log(`  CLIENT ${DEMO_CLIENT.email}`);
      process.exit(0);
    })
    .catch((err) => {
      console.error(err);
      process.exit(1);
    });
}
