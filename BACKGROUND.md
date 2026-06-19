# Background: Network Visibility Technologies

Network visibility refers to the ability to observe, capture, and analyze traffic flowing through a network. At the infrastructure level, network devices provide two fundamental mechanisms for making traffic available to monitoring tools:

```
                          Network Visibility
                                │
                ┌───────────────┴───────────────┐
                │                               │
       Traffic Mirroring                   Flow Export
      (full copy of packets)          (metadata about flows)
                │                               │
        ┌───────┼───────┐               ┌───────┼───────┐
        │       │       │               │       │       │
       TAP    SPAN   ERSPAN          NetFlow  IPFIX  sFlow
              RSPAN                  (Cisco)  (IETF) (sampled)
```

## Traffic Mirroring

Traffic mirroring creates an exact copy of network packets and delivers that copy to a monitoring destination, allowing inspection of full packet headers and payloads without disrupting the original traffic flow. It is essential for deep packet inspection, protocol debugging, and forensic analysis.

- **TAP (Test Access Point):** A passive hardware device inserted inline on a link that optically or electrically splits the traffic. TAPs provide 100% packet fidelity, introduce no latency, and require no switch configuration. They are considered the gold standard for reliable, forensic-grade monitoring in production environments.
- **SPAN (Switched Port Analyzer):** A software feature on managed switches that mirrors traffic from one or more source ports (or VLANs) to a designated destination port on the same switch. Simple to configure but limited to local ports, and SPAN traffic is deprioritized by the switch — packets may be dropped under high load.
- **RSPAN (Remote SPAN):** Extends SPAN across multiple switches within the same Layer 2 domain by transporting mirrored traffic over a dedicated RSPAN VLAN.
- **ERSPAN (Encapsulated Remote SPAN):** Encapsulates mirrored traffic in GRE tunnels, allowing it to traverse Layer 3 (routed) networks. This enables remote monitoring across data centers or cloud boundaries, at the cost of additional encapsulation overhead.

The general guidance is: **TAP where you can, SPAN where you must.** TAPs provide complete, unaltered copies of traffic, while SPAN ports offer convenience at the risk of dropped packets during congestion.

## Flow Export

Flow export technologies provide summarized metadata about network traffic without copying every raw packet. This approach is far more bandwidth-efficient than traffic mirroring and is suited for traffic analytics, capacity planning, billing, and anomaly detection.

Two distinct architectures exist within this category. **Stateful flow tracking** (NetFlow, IPFIX) maintains an in-memory flow cache on the device, aggregating packets into flows and exporting summarized records when flows expire. **Stateless packet sampling** (sFlow) randomly samples individual packets in hardware and immediately exports sampled headers — no flow state is maintained on the device. Each architecture offers different trade-offs between accuracy, scalability, and resource consumption.

- **NetFlow:** Developed by Cisco as a proprietary protocol for IP traffic flow information. The device maintains a flow cache, grouping packets into flows based on shared key fields (typically source/destination IP, ports, protocol, type of service, and input interface). When a flow expires due to inactivity, reaches a maximum active duration, or when the cache fills, a summarized record is exported to a collector. NetFlow v5 uses a fixed record format supporting only IPv4 and ingress traffic. NetFlow v9 introduced flexible, template-based records, decoupling the data structure from the protocol and adding support for IPv6, MPLS, and egress flows. Vendor-specific variants include Juniper's J-Flow and Huawei's NetStream, which are largely compatible with NetFlow v9.

- **IPFIX (IP Flow Information Export):** The IETF standard (RFC 7011) for flow data export, based on NetFlow v9. IPFIX provides a vendor-neutral, extensible, template-based format and is sometimes informally referred to as "NetFlow v10." It formalized the information model (RFC 7012), added support for variable-length fields, and defined a standard set of information elements, enabling interoperability across vendors and collector implementations.

- **sFlow (Sampled Flow):** A multi-vendor packet sampling technology originally described in RFC 3176 (Informational). The current version, sFlow v5, is maintained by the [sFlow.org](https://sflow.org/) consortium. Unlike NetFlow and IPFIX, sFlow does not maintain a flow cache. Instead, it combines two complementary mechanisms:
  - **Packet sampling:** The switching ASIC randomly samples packets at wire speed. A hardware counter decrements with each packet; when it reaches zero, the packet header is copied and forwarded to the sFlow agent. The counter then resets to a random value whose mean equals the configured sampling rate *N*, ensuring that on average one in every *N* packets is sampled with no CPU overhead on the switch.
  - **Counter polling:** At a configurable interval, the sFlow agent reads interface counters (byte counts, packet counts, errors) and includes them in the export stream, providing time-series statistics similar to SNMP polling.

  The sFlow agent encapsulates both packet samples and counter samples into UDP datagrams (IANA-assigned default port 6343) and sends them immediately to a collector. This stateless, hardware-accelerated design scales effectively across high-speed environments without the flow-cache exhaustion risks that can affect stateful protocols under adversarial traffic patterns such as DDoS attacks.

This project uses **sFlow** because SONiC includes native support for it through a containerized agent, requiring minimal configuration to enable network-wide telemetry.

## Software Packet Capture

Independent of the infrastructure-level mechanisms above, software tools can capture packets directly on a host's network interface. These tools hook into the operating system's network stack to record and analyze packets as they pass through:

- **libpcap / pcap:** The foundational library for packet capture on Unix-like systems. Most capture tools are built on top of it.
- **tcpdump:** A widely used command-line tool built on libpcap for quick, scriptable packet capture and filtering.
- **Wireshark:** A GUI-based protocol analyzer for deep packet inspection, with support for hundreds of protocols.
- **scapy:** A Python-based packet manipulation library that can capture, craft, decode, and inject packets. Highly flexible for scripting and automation.

What a capture tool can see depends on where it runs:

- **On an end host:** Only traffic destined to or originating from that host (plus broadcast and multicast traffic on the local segment).
- **On a software-based network device** (e.g., a virtual switch or router where the kernel handles forwarding): Transit traffic — packets being routed through the device between other hosts — is visible because it passes through the kernel's network stack before being forwarded.
- **On a hardware-accelerated switch** (e.g., a SONiC switch with a switching ASIC): Transit traffic is forwarded entirely in the ASIC at wire speed and never reaches user space. Only control-plane traffic that is punted to the CPU (ARP, LLDP, ICMP destined to the switch, routing protocol exchanges) is visible to software capture tools. To inspect data-plane transit traffic on such devices, traffic mirroring (SPAN or ERSPAN) must be configured to copy selected flows to the CPU or an external analyzer.

This project uses **scapy-based software capture** for per-interface diagnostics, PCAP generation, and real-time traffic streaming. In the GNS3 environment, SONiC devices forward traffic in the Linux kernel rather than a hardware ASIC, so transit traffic is visible to user-space tools on every interface without mirror configuration.

## Why Both?

Traffic mirroring, flow export, and software packet capture serve complementary roles. Mirroring and software capture provide the depth needed for protocol-level debugging and forensic analysis — mirroring at the infrastructure level, software capture at the host level. Flow export provides the breadth needed for continuous, network-wide traffic visibility. A typical operational workflow uses flow export for always-on monitoring and anomaly detection, then deploys targeted packet capture to investigate specific issues. This project implements both sFlow telemetry and scapy-based software capture to combine that breadth and depth within a single toolkit.
