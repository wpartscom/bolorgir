const std = @import("std");

pub fn build(b: *std.Build) void {
    const target = b.standardTargetOptions(.{});
    const optimize = b.standardOptimizeOption(.{ .preferred_optimize_mode = .ReleaseSafe });

    const test_hooks = b.option(bool, "test_hooks", "Export the test-only blg_test_* hooks (never in release artifacts)") orelse false;
    const diag_counters = b.option(bool, "diag_counters", "Keep the mask-path diagnostic counters (relaxed atomic adds); false compiles the increments out") orelse true;
    const build_options = b.addOptions();
    build_options.addOption(bool, "test_hooks", test_hooks);
    build_options.addOption(bool, "diag_counters", diag_counters);

    const strip = b.option(bool, "strip", "Omit debug information from the library (release artifacts: reproducible bytes)") orelse false;

    const core_mod = b.createModule(.{
        .root_source_file = b.path("src/root.zig"),
        .target = target,
        .optimize = optimize,
        .strip = strip,
    });
    core_mod.addOptions("build_options", build_options);

    const lib = b.addLibrary(.{
        .linkage = .dynamic,
        .name = "bolorgir",
        .root_module = core_mod,
        .version = .{ .major = 0, .minor = 1, .patch = 0 },
        // The self-hosted x86_64 backend miscompiles this code in Debug
        // (values kept in caller-saved registers across calls, e.g. a live
        // pointer in %r11/%rdx clobbered by callees such as __tls_get_addr).
        .use_llvm = true,
    });
    b.installArtifact(lib);

    const test_mod = b.createModule(.{
        .root_source_file = b.path("src/tests.zig"),
        .target = target,
        .optimize = optimize,
    });
    // The unit tests are the only place the test-only switches are used
    // (the certificate-cache differential, the forced verdicts, the budget
    // overrides), so the test build always compiles the hooks in: with the
    // release default the switches would silently do nothing and the
    // differentials would compare a build against itself.
    const test_build_options = b.addOptions();
    test_build_options.addOption(bool, "test_hooks", true);
    test_build_options.addOption(bool, "diag_counters", diag_counters);
    test_mod.addOptions("build_options", test_build_options);
    const unit_tests = b.addTest(.{ .root_module = test_mod, .use_llvm = true });
    const run_tests = b.addRunArtifact(unit_tests);
    const test_step = b.step("test", "Run core unit tests");
    test_step.dependOn(&run_tests.step);

    const exe_mod = b.createModule(.{
        .root_source_file = null,
        .target = target,
        .optimize = optimize,
        .link_libc = true,
    });
    const c_example = b.addExecutable(.{ .name = "c_client", .root_module = exe_mod });
    exe_mod.addCSourceFile(.{ .file = b.path("examples/c_client.c"), .flags = &.{"-std=c99"} });
    exe_mod.addIncludePath(b.path("include"));
    exe_mod.linkLibrary(lib);
    const example_step = b.step("example-c", "Build the C client example");
    example_step.dependOn(&b.addInstallArtifact(c_example, .{}).step);

    const fuzz_mod = b.createModule(.{
        .root_source_file = b.path("src/fuzz_main.zig"),
        .target = target,
        .optimize = optimize,
    });
    fuzz_mod.addOptions("build_options", build_options);
    const fuzz_exe = b.addExecutable(.{ .name = "blg_fuzz", .root_module = fuzz_mod, .use_llvm = true });
    const install_fuzz = b.addInstallArtifact(fuzz_exe, .{});
    const fuzz_build_step = b.step("fuzz-build", "Build the T4 fuzz runner");
    fuzz_build_step.dependOn(&install_fuzz.step);
    const run_fuzz = b.addRunArtifact(fuzz_exe);
    run_fuzz.addPassthruArgs();
    const fuzz_step = b.step("fuzz", "Run the T4 fuzz campaigns");
    fuzz_step.dependOn(&run_fuzz.step);
}
