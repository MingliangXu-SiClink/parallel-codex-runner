import SwiftUI

struct ComputerSettingsView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 12) {
                Image(systemName: "desktopcomputer")
                    .appScaledSystemFont(size: 22, weight: .medium)
                    .foregroundStyle(model.appAccentColor.color)
                    .frame(width: 34, height: 34)
                VStack(alignment: .leading, spacing: 2) {
                    Text(model.appLanguage.text("设备", "Computers"))
                        .appScaledTextStyle(.title2, weight: .semibold)
                    Text(model.appLanguage.text(
                        "选择要管理的设备；默认对话设置和存储空间按设备分别保存。",
                        "Choose a computer to manage. Conversation defaults and storage are kept per computer."
                    ))
                    .appScaledTextStyle(.subheadline)
                    .foregroundStyle(.secondary)
                }
                Spacer()
                Button {
                    Task { await model.refresh(forceMachineDirectoryRefresh: true) }
                } label: {
                    Image(systemName: "arrow.clockwise")
                }
                .buttonStyle(.borderless)
                .help(model.appLanguage.text("刷新设备", "Refresh computers"))
            }
            .padding(20)

            Divider()

            List {
                if model.machines.isEmpty {
                    Label(
                        model.appLanguage.text("尚未连接设备", "No computers connected"),
                        systemImage: "desktopcomputer.trianglebadge.exclamationmark"
                    )
                    .foregroundStyle(.secondary)
                } else {
                    ForEach(model.machines) { machine in
                        Button {
                            guard machine.deviceId != model.selectedMachineID else { return }
                            Task { await model.selectMachine(machine.deviceId) }
                        } label: {
                            HStack(spacing: 12) {
                                Image(systemName: machine.online == false
                                    ? "desktopcomputer.trianglebadge.exclamationmark"
                                    : "desktopcomputer")
                                    .appScaledTextStyle(.title3)
                                    .foregroundStyle(
                                        machine.deviceId == model.selectedMachineID
                                            ? model.appAccentColor.color
                                            : Color.secondary
                                    )
                                    .frame(width: 28)
                                VStack(alignment: .leading, spacing: 3) {
                                    Text(machine.name)
                                        .appScaledTextStyle(.body, weight: .medium)
                                    Text(machineStatus(machine))
                                        .appScaledTextStyle(.caption)
                                        .foregroundStyle(
                                            machine.online == false ? .orange : .secondary
                                        )
                                }
                                Spacer()
                                if machine.deviceId == model.selectedMachineID {
                                    Label(
                                        model.appLanguage.text("当前设备", "Current"),
                                        systemImage: "checkmark.circle.fill"
                                    )
                                    .appScaledTextStyle(.caption, weight: .medium)
                                    .foregroundStyle(model.appAccentColor.color)
                                }
                            }
                            .padding(.horizontal, 10)
                            .frame(minHeight: 46)
                            .background(
                                machine.deviceId == model.selectedMachineID
                                    ? model.appAccentColor.color.opacity(0.12)
                                    : Color.clear,
                                in: RoundedRectangle(cornerRadius: 7, style: .continuous)
                            )
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        .disabled(model.machineSelectionInProgress)
                        .listRowBackground(Color.clear)
                        .listRowSeparator(.hidden)
                    }
                }
            }
            .listStyle(.inset(alternatesRowBackgrounds: false))

            Divider()
            HStack {
                Text(model.appLanguage.text(
                    "切换设备后，“对话”和“存储空间”页面会自动显示该设备的设置。",
                    "The Conversation and Storage tabs follow the selected computer."
                ))
                .appScaledTextStyle(.caption)
                .foregroundStyle(.secondary)
                Spacer()
                Button {
                    model.machinePairingPresented = true
                } label: {
                    Label(
                        model.appLanguage.text("连接新设备", "Connect Computer"),
                        systemImage: "plus"
                    )
                }
                .buttonStyle(.bordered)
                Button {
                    model.pairingPresented = true
                } label: {
                    Label(
                        model.relay.hasConnectedPhone
                            ? model.appLanguage.text("连接另一台手机", "Connect Another Phone")
                            : model.appLanguage.text("配对手机", "Pair Phone"),
                        systemImage: "qrcode"
                    )
                }
                .buttonStyle(.borderedProminent)
            }
            .padding(14)
            .background(.bar)
        }
    }

    private func machineStatus(_ machine: RelayMachine) -> String {
        if machine.online == false {
            return model.appLanguage.text("设备未连接", "Computer offline")
        }
        return model.appLanguage.text("服务端已连接", "Server connected")
    }
}
