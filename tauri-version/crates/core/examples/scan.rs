fn main() {
    let r = devdock_core::cleaner::scan(&Default::default(), &Default::default(), &|m| eprintln!("{m}")).unwrap();
    for i in &r.items {
        println!("{:<6} {:>6} MB  {}", i.level, (i.size / 1048576).max(1), i.title);
    }
}
