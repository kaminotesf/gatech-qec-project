import sys
import numpy as np

from qiskit import QuantumRegister, AncillaRegister, ClassicalRegister, QuantumCircuit
from qiskit.quantum_info import Pauli, Clifford
from qiskit.circuit.library import HGate
from qiskit.circuit.classical import expr

class LogicalCircuit(QuantumCircuit):
    def __init__(
            self,
            n_logical_qubits,
            label,
            stabilizer_tableau,
            name: str | None = None,
        ):

        # Quantum error correcting code preparation
        self.n_logical_qubits = n_logical_qubits

        self.stabilizer_tableau = stabilizer_tableau
        self.n_stabilizers = len(self.stabilizer_tableau)
        self.n_physical_qubits = len(self.stabilizer_tableau[0])

        self.n, self.k, self.d = label
        assert self.n == self.n_physical_qubits, f"Code label n ({self.n}) does not match individual stabilizer length ({self.n_physical_qubits})."

        self.generate_code()

        # Auxiliary data preparation
        self.n_ancilla_qubits = self.n_stabilizers//2
        self.n_measure_qubits = self.n_ancilla_qubits
        
        self.flagged_stabilizers_1 = []
        self.flagged_stabilizers_2 = []
        self.x_stabilizers = []
        self.z_stabilizers = []

        self.logical_qregs = []
        self.ancilla_qregs = []
        self.logical_op_qregs = []
        self.enc_verif_cregs = []
        self.curr_syndrome_cregs = []
        self.prev_syndrome_cregs = []
        self.flagged_syndrome_diff_cregs = []
        self.unflagged_syndrome_diff_cregs = []
        self.pauli_frame_cregs = []
        self.final_measurement_cregs = []
        self.output_creg = ClassicalRegister(self.n_logical_qubits, name="output")

        # The underlying QuantumCircuit is generated by calling super()
        super().__init__(name=name)
        self.add_logical_qubits(self.n_logical_qubits)
        super().add_register(self.output_creg)
        self.group_stabilizers()

        # @TODO - find alternative, possibly by implementing upstream
        # Create setter qreg for purpose of setting classical bits dynamically
        self.cbit_setter_qreg = QuantumRegister(2, name="qsetter")
        self.add_register(self.cbit_setter_qreg)
        super().x(self.cbit_setter_qreg[1])

    # @TODO
    @classmethod
    def from_physical_circuit(cls, physical_circuit, label, stabilizer_tableau, name=None):
        logical_circuit = cls(physical_circuit.num_qubits, label, stabilizer_tableau, name)
        
        logical_circuit_data = []
        for i in range(len(physical_circuit.depth())):
            circuit_instruction = physical_circuit.data[i]
            operation = circuit_instruction[0]
            qubits = circuit_instruction[1]
            clbits = circuit_instruction[2]
            
            # @TODO - get other pieces of data from the circuit instruction, if any
            # ...

            # @TODO - this doesn't make sense, but the idea is to have some modularized function that knows how to add any quantum operation to the circuit
            logical_circuit.append(circuit_instruction)

        # @TODO - right now, this doesn't exactly make sense either because we haven't defined the notion of LogicalCircuit data, but this might be something we want to implement and use at some point, so it's worth thinking about
        logical_circuit.data = logical_circuit_data
    
        return logical_circuit
        
    def add_logical_qubits(self, logical_qubit_count):
        current_logical_qubit_count = len(self.logical_qregs)

        for i in range(current_logical_qubit_count, current_logical_qubit_count + logical_qubit_count):
            # Physical qubits for logical qubit
            logical_qreg_i = QuantumRegister(self.n_physical_qubits, name=f"qlog{i}")
            # Ancilla qubits needed for measurements
            ancilla_qreg_i = AncillaRegister(self.n_ancilla_qubits, name=f"qanc{i}")
            # Ancilla qubits needed for logical operations
            logical_op_qreg_i = AncillaRegister(1, name=f"qlogical_op{i}")
            # Classical bits needed for encoding verification
            enc_verif_creg_i = ClassicalRegister(1, name=f"cenc_verif{i}")
            # Classical bits needed for measurements
            curr_syndrome_creg_i = ClassicalRegister(self.n_measure_qubits, name=f"ccurr_syndrome{i}")
            # Classical bits needed for previous syndrome measurements
            prev_syndrome_creg_i = ClassicalRegister(self.n_stabilizers, name=f"cprev_syndrome{i}")
            # Classical bits needed for flagged syndrome difference measurements
            flagged_syndrome_diff_creg_i = ClassicalRegister(self.n_stabilizers, name=f"cflagged_syndrome_diff{i}")
            # Classical bits needed for unflagged syndrome difference measurements
            unflagged_syndrome_diff_creg_i = ClassicalRegister(self.n_stabilizers, name=f"cunflagged_syndrome_diff{i}")
            # Classical bits needed to track the Pauli Frame
            pauli_frame_creg_i = ClassicalRegister(2, name=f"cpauli_frame{i}")
            # Classical bits needed to take measurements of the final state of the logical qubit
            final_measurement_creg_i = ClassicalRegister(self.n_physical_qubits, name=f"cfinal_meas{i}")

            # Add new registers to storage lists
            self.logical_qregs.append(logical_qreg_i)
            self.ancilla_qregs.append(ancilla_qreg_i)
            self.logical_op_qregs.append(logical_op_qreg_i)
            self.enc_verif_cregs.append(enc_verif_creg_i)
            self.curr_syndrome_cregs.append(curr_syndrome_creg_i)
            self.prev_syndrome_cregs.append(prev_syndrome_creg_i)
            self.flagged_syndrome_diff_cregs.append(flagged_syndrome_diff_creg_i)
            self.unflagged_syndrome_diff_cregs.append(unflagged_syndrome_diff_creg_i)
            self.pauli_frame_cregs.append(pauli_frame_creg_i)
            self.final_measurement_cregs.append(final_measurement_creg_i)
            
            # Add new registers to quantum circuit
            super().add_register(logical_qreg_i)
            super().add_register(ancilla_qreg_i)
            super().add_register(logical_op_qreg_i)
            super().add_register(enc_verif_creg_i)
            super().add_register(curr_syndrome_creg_i)
            super().add_register(prev_syndrome_creg_i)
            super().add_register(flagged_syndrome_diff_creg_i)
            super().add_register(unflagged_syndrome_diff_creg_i)
            super().add_register(pauli_frame_creg_i)
            super().add_register(final_measurement_creg_i)

    ############################################
    ##### Quantum error correction methods #####
    ############################################

    def group_stabilizers(self):
        # @TODO - determine how stabilizers are generally selected for flagged measurements
        #       - the below is a heuristic which happens to work for the Steane code and potentially all CSS codes, but maybe not all stabilizer codes in general
        
        # Take the middle k stabilizers
        k = self.n_stabilizers//2
        self.flagged_stabilizers_1 = [s for s in range(self.n_stabilizers) if s < k - k//2 - 1 or s > k + k//2 - 1]
        self.flagged_stabilizers_2 = [s for s in range(self.n_stabilizers) if s < k - k//2 - 1 or s > k + k//2 - 1]

        for i in range(self.n_stabilizers):
            if 'X' in self.stabilizer_tableau[i]:
                self.x_stabilizers.append(i)
            if 'Z' in self.stabilizer_tableau[i]:
                self.z_stabilizers.append(i)

    # Function which generates encoding circuit and logical operators for a given tableau
    def generate_code(self):
        m = len(self.stabilizer_tableau)
    
        # Step 1: Assemble generator matrix
        G = np.zeros((2, m, self.n))
        for i, stabilizer in enumerate(self.stabilizer_tableau):
            for j, pauli_j in enumerate(stabilizer):
                if pauli_j == "X":
                    G[0, i, j] = 1
                elif pauli_j == "Z":
                    G[1, i, j] = 1
                elif pauli_j == "Y":
                    G[:, i, j] = 1
    
        # Step 2: Perform Gaussian reduction in base 2
        row = 0
        for col in range(self.n):
            pivot_row = None
            for i in range(row, m):
                if G[0, i, col] == 1:
                    pivot_row = i
                    break
            
            if pivot_row is None:
                continue
            
            G[:, [row, pivot_row]] = G[:, [pivot_row, row]]
    
            # Flip any other rows with a "1" in the same column
            for i in range(m):
                if i != row and G[0, i, col] == 1:
                    G[:, i] = G[:, i].astype(int) ^ G[:, row].astype(int)
                    # G[0, i] = G[0, i].astype(int) ^ G[0, row].astype(int)
                    # G[1, i] = G[1, i].astype(int) ^ G[1, row].astype(int)
    
            # Move to the next row, if we haven't reached the end of the matrix
            row += 1
            if row >= m:
                break

        self.G = G
    
        # Step 3: Construct logical operators using Pauli vector representations due to Gottesmann (1997)
        r = np.linalg.matrix_rank(self.G[0])
        A_2 = self.G[0, 0:r, m:self.n] # r x k
        C_1 = self.G[1, 0:r, r:m] # r x m-r
        C_2 = self.G[1, 0:r, m:self.n] # r x k
        E_2 = self.G[1, r:m, m:self.n] # m-r x k
    
        self.LogicalXVector = np.block([
            [[np.zeros((self.k, r)), E_2.T,                        np.eye(self.k, self.k)    ]],
            [[E_2.T @ C_1.T + C_2.T,      np.zeros((self.k, m-r)), np.zeros((self.k, self.k))]]
        ])

        # Create Logical X circuit corresponding to X's and Z's at 1's in Pauli vector
        LogicalXCircuit = QuantumCircuit(self.n)
        for i in range(self.k):
            # X part
            for q, bit in enumerate(self.LogicalXVector[0][i]):
                if bit == 1:
                    LogicalXCircuit.x(q)
            # Z part
            for q, bit in enumerate(self.LogicalXVector[1][i]):
                if bit == 1:
                    LogicalXCircuit.z(q)
        self.LogicalXGate = LogicalXCircuit.to_gate(label="$X_L$")

        self.LogicalZVector = np.block([
            [[np.zeros((self.k, r)), np.zeros((self.k, m-r)), np.zeros((self.k, self.k))]],
            [[A_2.T,                 np.zeros((self.k, m-r)), np.eye(self.k, self.k)    ]]
        ])

        # Create Logical Z circuit corresponding to X's and Z's at 1's in Pauli vector
        LogicalZCircuit = QuantumCircuit(self.n)
        for i in range(self.k):
            # X part
            for q, bit in enumerate(self.LogicalZVector[0][i]):
                if bit == 1:
                    LogicalZCircuit.x(q)
            # Z part
            for q, bit in enumerate(self.LogicalZVector[1][i]):
                if bit == 1:
                    LogicalZCircuit.z(q)
        self.LogicalZGate = LogicalZCircuit.to_gate(label="$Z_L$")

        LogicalYCircuit = LogicalXCircuit.compose(LogicalZCircuit)
        self.LogicalYGate = LogicalYCircuit.to_gate(label="$Y_L$")

        # Create Logical H circuit using Childs and Wiebe's linear combination of unitaries method
        LogicalHCircuit_LCU = QuantumCircuit(self.n + 1)
        LogicalHCircuit_LCU.h(self.n)
        
        LogicalHCircuit_LCU.append(self.LogicalXGate.control(1), [self.n, *list(range(self.n))])

        LogicalHCircuit_LCU.x(self.n)
        LogicalHCircuit_LCU.append(self.LogicalZGate.control(1), [self.n, *list(range(self.n))])
        LogicalHCircuit_LCU.x(self.n)
        
        LogicalHCircuit_LCU.h(self.n)
        self.LogicalHGate_LCU = LogicalHCircuit_LCU.to_gate(label="$H_L$")

        # @TODO - Logical S

        # @TODO - Logical CX
    
        # Step 4: Apply the respective stabilizers
        encoding_circuit = QuantumCircuit(self.n)
        for i in range(self.k):
            for j in range(r, self.n-self.k):
                if self.LogicalXVector[0, i, j]:
                    encoding_circuit.cx(self.n-self.k+i, j)
        
        for i in range(r):
            encoding_circuit.h(i)
            for j in range(self.n):
                if i != j:
                    if self.G[0, i, j]:
                        encoding_circuit.cx(i, j)
                    elif self.G[1, i, j]:
                        encoding_circuit.cz(i, j)
                    elif self.G[0, i, j] and self.G[1, i, j]:
                        encoding_circuit.cx(i, j)
                        encoding_circuit.cz(i, j)
     
        self.encoding_gate = encoding_circuit.to_gate(label="$U_{enc}$")

    # Encodes logical qubits for a given number of iterations
    def encode(self, *qubits, max_iterations=1, initial_states=None):
        """
        Prepare logical qubit(s) in the specified initial state
        """
        
        if initial_states is None or len(qubits) != len(initial_states):
            raise ValueError("Number of qubits should equal number of initial states if initial states are provided")
        
        for q, init_state in zip(qubits, initial_states):
            # Preliminary physical qubit reset
            super().reset(self.logical_qregs[q])
            
            # Initial encoding
            super().append(self.encoding_gate, self.logical_qregs[q])

            # CNOT from physical qubits to ancilla(e)
            super().cx(self.logical_qregs[q][1], self.ancilla_qregs[q][0])
            super().cx(self.logical_qregs[q][3], self.ancilla_qregs[q][0])
            super().cx(self.logical_qregs[q][5], self.ancilla_qregs[q][0])

            # Measure ancilla(e)
            super().measure(self.ancilla_qregs[q][0], self.enc_verif_cregs[q][0])

            for _ in range(max_iterations - 1):
                # If the ancilla stores a 1, reset the entire logical qubit and redo
                with super().if_test((self.enc_verif_cregs[q][0], 1)):
                    super().reset(self.logical_qregs[q])

                    # Initial encoding
                    super().append(self.encoding_gate, self.logical_qregs[q])

                    # CNOT from (Z1 Z3 Z5) to ancilla
                    super().cx(self.logical_qregs[q][1], self.ancilla_qregs[q][0])
                    super().cx(self.logical_qregs[q][3], self.ancilla_qregs[q][0])
                    super().cx(self.logical_qregs[q][5], self.ancilla_qregs[q][0])

                    # Measure ancilla
                    super().measure(self.ancilla_qregs[q][0], self.enc_verif_cregs[q][0])
        
            # Reset ancilla qubit
            super().reset(self.ancilla_qregs[q][0])

            # Flip qubits if necessary
            if init_state == 1:
                self.x(q)
            elif init_state != 0:
                raise ValueError("Initial state should be either 0 or 1 (arbitrary statevectors not yet supported)!")
        
        return True

    # Reset all ancillas associated with specified logical qubits
    def reset_ancillas(self, logical_qubit_indices=None):
        if logical_qubit_indices is None or len(logical_qubit_indices) == 0:
            logical_qubit_indices = list(range(self.n_logical_qubits))
        
        for q in logical_qubit_indices:
            self.reset(self.ancilla_qregs[q])

    # Measure specified specifiers to the circuit as controlled Pauli operators
    def measure_stabilizers(self, logical_qubit_indices=None, stabilizer_indices=None):
        if logical_qubit_indices is None or len(logical_qubit_indices) == 0:
            logical_qubit_indices = list(range(self.n_logical_qubits))
        
        if stabilizer_indices is None or len(logical_qubit_indices) == 0:
            stabilizer_indices = list(range(self.n_stabilizers))
        
        for q in logical_qubit_indices:
            for p in range(self.n_physical_qubits):
                if p == (self.n_physical_qubits-1)-1:
                    super().cz(self.ancilla_qregs[q][0], self.ancilla_qregs[q][1])
                
                for s, stabilizer_index in enumerate(stabilizer_indices):
                    stabilizer = self.stabilizer_tableau[stabilizer_index]
                    stabilizer_pauli = Pauli(stabilizer[p])
                    measurement_pauli = stabilizer_pauli.evolve(Clifford(HGate()))
                    
                    CPauliInstruction = measurement_pauli.to_instruction().control(1)
                    self.append(CPauliInstruction, [self.logical_qregs[q][p], self.ancilla_qregs[q][s]])

                if p == 0:
                    super().cz(self.ancilla_qregs[q][0], self.ancilla_qregs[q][2])

    # Measure flagged or unflagged syndrome differences for specified logical qubits and stabilizers
    def measure_syndrome_diff(self, logical_qubit_indices=None, stabilizer_indices=None, flagged=False):
        if logical_qubit_indices is None or len(logical_qubit_indices) == 0:
            logical_qubit_indices = list(range(self.n_logical_qubits))
        
        if stabilizer_indices is None or len(stabilizer_indices) == 0:
            stabilizer_indices = list(range(self.n_stabilizers))

        for q in logical_qubit_indices:
            syndrome_diff_creg = self.flagged_syndrome_diff_cregs[q] if flagged else self.unflagged_syndrome_diff_cregs[q]

            # Apply and measure stabilizers for the desired syndrome
            self.measure_stabilizers(logical_qubit_indices=[q], stabilizer_indices=stabilizer_indices)
            for n in range(self.n_ancilla_qubits):
                super().measure(self.ancilla_qregs[q][n], self.curr_syndrome_cregs[q][n])
        
            # Determine the syndrome difference
            for n in range(len(stabilizer_indices)):
                with self.if_test(self.cbit_xor([self.curr_syndrome_cregs[q][n], self.prev_syndrome_cregs[q][stabilizer_indices[n]]])) as _else:
                    self.set_cbit(syndrome_diff_creg[stabilizer_indices[n]], 1)
                with _else:
                    self.set_cbit(syndrome_diff_creg[stabilizer_indices[n]], 0)
        
        self.reset_ancillas(logical_qubit_indices=logical_qubit_indices)

    # @TODO - allow configuration of QEC cycling
    def configure_qec_cycle(self, **config):
        raise NotImplementedError("QEC cycle configuration has not yet been implemented.")

    def perform_qec_cycle(self, logical_qubit_indices=None):
        if logical_qubit_indices is None or len(logical_qubit_indices) == 0:
            logical_qubit_indices = list(range(self.n_logical_qubits))

        for q in logical_qubit_indices:
            super().reset(self.ancilla_qregs[q])

            # Perform first flagged syndrome measurements
            self.measure_syndrome_diff(logical_qubit_indices=[q], stabilizer_indices=self.flagged_stabilizers_1, flagged=True)
        
            # If no change in syndrome, perform second flagged syndrome measurement
            with self.if_test(expr.equal(self.flagged_syndrome_diff_cregs[q], 0)):
                self.measure_syndrome_diff(logical_qubit_indices=[q], stabilizer_indices=self.flagged_stabilizers_2, flagged=True)
        
            # If change in syndrome, perform unflagged syndrome measurement, decode, and correct
            with self.if_test(expr.not_equal(self.flagged_syndrome_diff_cregs[q], 0)):
                self.measure_syndrome_diff(logical_qubit_indices=[q], stabilizer_indices=self.x_stabilizers, flagged=False)
                self.measure_syndrome_diff(logical_qubit_indices=[q], stabilizer_indices=self.z_stabilizers, flagged=False)
        
                self.apply_decoding(logical_qubit_indices=[q], stabilizer_indices=self.x_stabilizers, with_flagged=False)
                self.apply_decoding(logical_qubit_indices=[q], stabilizer_indices=self.z_stabilizers, with_flagged=False)
                self.apply_decoding(logical_qubit_indices=[q], stabilizer_indices=self.x_stabilizers, with_flagged=True)
                self.apply_decoding(logical_qubit_indices=[q], stabilizer_indices=self.z_stabilizers, with_flagged=True)
        
                # Update previous syndrome
                for n in range(self.n_stabilizers):
                    with self.if_test(expr.lift(self.unflagged_syndrome_diff_cregs[q][n])):
                        self.cbit_not(self.prev_syndrome_cregs[q][n])

    # @TODO - determine appropriate syndrome decoding mappings dynamically
    def apply_decoding(self, logical_qubit_indices, stabilizer_indices, with_flagged):
        for q in logical_qubit_indices:
            syn_diff = [self.unflagged_syndrome_diff_cregs[q][x] for x in stabilizer_indices]
            # Determines index of pauli frame to be modified
            pf_ind = 0 if 'X' in self.stabilizer_tableau[stabilizer_indices[0]] else 1

            # Decoding sequence with flagged syndrome
            if with_flagged:
                flag_diff = [self.flagged_syndrome_diff_cregs[q][x] for x in stabilizer_indices]
                with super().if_test(expr.bit_and(self.cbit_and(flag_diff, [1, 0, 0]), self.cbit_and(syn_diff, [0, 1, 0]))):
                    self.cbit_not(self.pauli_frame_cregs[q][pf_ind])
                with super().if_test(expr.bit_and(self.cbit_and(flag_diff, [1, 0, 0]), self.cbit_and(syn_diff, [0, 0, 1]))):
                    self.cbit_not(self.pauli_frame_cregs[q][pf_ind])
                with super().if_test(expr.bit_and(self.cbit_and(flag_diff, [0, 1, 1]), self.cbit_and(syn_diff, [0, 0, 1]))):
                    self.cbit_not(self.pauli_frame_cregs[q][pf_ind])
            
            # Unflagged decoding sequence
            else:
                with super().if_test(self.cbit_and(syn_diff, [0, 1, 0])):
                    self.cbit_not(self.pauli_frame_cregs[q][pf_ind])
                with super().if_test(self.cbit_and(syn_diff, [0, 1, 1])):
                    self.cbit_not(self.pauli_frame_cregs[q][pf_ind])
                with super().if_test(self.cbit_and(syn_diff, [0, 0, 1])):
                    self.cbit_not(self.pauli_frame_cregs[q][pf_ind])

    def measure(self, logical_qubit_indices, cbit_indices):
        if len(logical_qubit_indices) != len(cbit_indices):
            raise ValueError("Number of qubits should equal number of classical bits")
        
        for q, c in zip(logical_qubit_indices, cbit_indices):
            # Measurement of state
            for n in range(self.n_physical_qubits):
                super().measure(self.logical_qregs[q][n], self.final_measurement_cregs[q][n])

            with super().if_test(self.cbit_xor([self.final_measurement_cregs[q][x] for x in [4,5,6]])):
                self.set_cbit(self.output_creg[c], 1)

            # Final syndrome
            for n in range(self.n_ancilla_qubits):
                stabilizer = self.stabilizer_tableau[self.z_stabilizers[n]]
                s_indices = []
                for i in range(len(stabilizer)):
                    if stabilizer[i] == 'Z':
                        s_indices.append(i)

                with super().if_test(self.cbit_xor([self.final_measurement_cregs[q][z] for z in s_indices])):
                    self.set_cbit(self.curr_syndrome_cregs[q][n], 1)

            # Final syndrome diff
            for n in range(self.n_ancilla_qubits):
                with super().if_test(self.cbit_xor([self.curr_syndrome_cregs[q][n], self.prev_syndrome_cregs[q][self.z_stabilizers[n]]])) as _else:
                    self.set_cbit(self.unflagged_syndrome_diff_cregs[q][self.z_stabilizers[n]], 1)
                with _else:
                    self.set_cbit(self.unflagged_syndrome_diff_cregs[q][self.z_stabilizers[n]], 0)

            # Final correction
            self.apply_decoding([q], self.z_stabilizers, with_flagged=False)
            with super().if_test(expr.lift(self.pauli_frame_cregs[q][1])):
                self.cbit_not(self.output_creg[c])

    ######################################
    ##### Logical quantum operations #####
    ######################################

    # @TODO - generalize logical quantum operations using stabilizers

    def h(self, *targets, method="LCU"):
        """
        Logical Hadamard gate
        """

        if len(targets) == 1 and hasattr(targets[0], "__iter__"):
            targets = targets[0]

        if method == "LCU":
            for t in targets:
                super().append(self.LogicalHGate_LCU, [self.logical_op_qregs[t][0]] + self.logical_qregs[t][:])
            
            # @TODO - perform resets after main operation is complete to allow for faster(?) parallel operation
            for t in targets:
                # @TODO - determine whether extra reset is necessary at the end
                super().reset(self.logical_op_qregs[t])
        else:
            raise ValueError(f"'{method}' is not a valid method for the logical Hadamard gate")
    
    def x(self, *targets):
        """
        Logical PauliX gate
        """

        if len(targets) == 1 and hasattr(targets[0], "__iter__"):
            targets = targets[0]
        
        for t in targets:
            super().append(self.LogicalXGate, self.logical_qregs[t])
    
    def y(self, *targets):
        """
        Logical PauliY gate
        """

        self.z(targets)
        self.x(targets)
    
    def z(self, *targets):
        """
        Logical PauliZ gate
        """

        if len(targets) == 1 and hasattr(targets[0], "__iter__"):
            targets = targets[0]

        for t in targets:
            super().append(self.LogicalZGate, self.logical_qregs[t])
    
    def s(self, *targets):
        """
        Logical S (pi/4 phase) gate
        """

        for t in targets:
            super().s(self.logical_qregs[t])
            super().s(self.logical_qregs[t])
            super().s(self.logical_qregs[t])
    
    def cx(self, control, *targets):
        """
        Logical Controlled-PauliX gate
        """

        if len(targets) == 1 and hasattr(targets[0], "__iter__"):
            targets = targets[0]

        for t in targets:
            super().append(self.LogicalXGate.control(1), self.logical_qregs[control][:] + self.logical_qregs[t][:])

    def mcmt(self, controls, targets):
        """
        Logical Multi-Controll Multi-Target gate
        """

        if len(controls) == 1 and hasattr(controls[0], "__iter__"):
            controls = controls[0]

        if len(targets) == 1 and hasattr(targets[0], "__iter__"):
            targets = targets[0]

        control_qubits = [self.logical_qregs[c][:] for c in controls]
        target_qubits = [self.logical_qregs[t][:] for t in targets]

        assert set(control_qubits).isdisjoint(target_qubits), "Qubit(s) specified as both control and target"
        
        super().append(self.LogicalXGate.control(len(controls)), control_qubits + target_qubits)

    ###########################
    ##### Utility methods #####
    ###########################

    # Adds a desired error for testing
    def add_error(self, l_ind, p_ind, type):
        if type == 'X':
            super().x(self.logical_qregs[l_ind][p_ind])
        if type == 'Z':
            super().z(self.logical_qregs[l_ind][p_ind])

    # @TODO - find alternative to classical methods, possibly by implementing upstream

    # Set values of classical bits
    def set_cbit(self, cbit, value):
        if value == 0:
            super().measure(self.cbit_setter_qreg[0], cbit)
        else:
            super().measure(self.cbit_setter_qreg[1], cbit)

    # Performs a NOT statement on a classical bit
    def cbit_not(self, cbit):
        with self.if_test(expr.lift(cbit)) as _else:
            self.set_cbit(cbit, 0)
        with _else:
            self.set_cbit(cbit, 1)
    
    # Performs AND and NOT statements on multiple classical bits, e.g. (~c[0] & ~c[1] & c[2])
    def cbit_and(self, cbits, values):
        result = expr.bit_not(cbits[0]) if values[0] == 0 else expr.lift(cbits[0])
        for n in range(len(cbits)-1):
            result = expr.bit_and(result, expr.bit_not(cbits[n+1])) if values[n+1] == 0 else expr.bit_and(result, cbits[n+1])
        return result
    
    # XOR multiple classical bits
    def cbit_xor(self, cbits):
        result = expr.lift(cbits[0])
        for n in range(len(cbits)-1):
            result = expr.bit_xor(result, cbits[n+1])
        return result
